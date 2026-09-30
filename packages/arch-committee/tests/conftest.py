"""A data-driven fake committee: its answers are derived from the plan text it receives (not canned), so the tests
exercise the real data flow plan -> prompt -> findings -> report."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from arch_committee.llm import LLMError, Usage
from arch_committee.plan_parser import PlanModel, load_plan

ROOT = Path(__file__).resolve().parents[3]
PLANS = ROOT / "examples" / "plans"


def plan_path(name: str) -> Path:
    return PLANS / f"{name}.json"


@pytest.fixture(params=["public-bucket", "rds-single-az", "nat-per-subnet"])
def example_plan(request: pytest.FixtureRequest) -> tuple[str, PlanModel]:
    return request.param, load_plan(plan_path(request.param))


def finding(title: str, severity: str, resource: str, evidence: list[tuple[str, str]], cause: str, fix: str,
            risk: str = "low", tags: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    return {
        "title": title,
        "severity": severity,
        "resource": resource,
        "evidence": [{"kind": k, "detail": d} for k, d in evidence],
        "root_cause": cause,
        "suggested_fix": {"summary": fix, "steps": [fix]},
        "risk_of_fix": risk,
        "tags": tags or [],
    }


class FakeCommittee:
    """Scripted ``LLM``. Findings come from inspecting the redacted plan JSON embedded in the prompt."""

    def __init__(
        self,
        *,
        fail: frozenset[str] = frozenset(),
        invalid: frozenset[str] = frozenset(),
        moderator_mode: str = "normal",
        extra_unknown_resource: bool = False,
        tokens_per_call: int = 600,
    ) -> None:
        self.fail, self.invalid = fail, invalid
        self.moderator_mode = moderator_mode
        self.extra_unknown_resource = extra_unknown_resource
        self.tokens_per_call = tokens_per_call
        self.calls: list[dict[str, Any]] = []

    # ---- LLM interface ----------------------------------------------------------------------------------------
    def converse_tool(
        self, *, system: str, user: str, tool_name: str, tool_description: str, schema: dict[str, Any], max_tokens: int
    ) -> tuple[dict[str, Any], Usage]:
        agent = re.search(r"# Agent: (\w+)", system).group(1)  # type: ignore[union-attr]
        self.calls.append({"agent": agent, "tool": tool_name, "system": system, "user": user, "schema": schema})
        if agent in self.fail:
            raise LLMError(f"simulated outage for {agent}")
        usage = Usage(self.tokens_per_call, 100)
        if agent in self.invalid:
            return {"findings": [{"title": "", "severity": "catastrophic"}]}, usage
        if tool_name == "report_findings":
            return {"findings": self._round1(agent, self._plan(user))}, usage
        if tool_name == "respond_to_findings":
            return {"challenges": self._round2(agent, self._section(user, "findings"))}, usage
        return self._moderate(user), usage

    # ---- helpers ----------------------------------------------------------------------------------------------
    @staticmethod
    def _section(user: str, tag: str) -> Any:
        return json.loads(user.split(f"<{tag}>\n", 1)[1].split(f"\n</{tag}>", 1)[0])

    def _plan(self, user: str) -> dict[str, Any]:
        plan: dict[str, Any] = self._section(user, "plan")
        return plan

    # ---- round 1: each specialist looks at the plan from its angle ---------------------------------------------
    def _round1(self, agent: str, plan: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        res = plan["resources"]
        env = next((r["attributes"]["tags"].get("Environment") for r in res if r["attributes"].get("tags")), "unknown")
        for r in res:
            a, addr, typ = r["attributes"], r["address"], r["type"]
            if agent == "security":
                if typ == "aws_s3_bucket_policy" and '"Principal":"*"' in a.get("policy", ""):
                    out.append(finding("Bucket S3 legible por cualquiera (Principal *)", "critical", addr,
                        [("policy", "La política concede s3:GetObject a Principal \"*\"")],
                        "La política del bucket es pública.", "Restringir el Principal y activar el bloqueo de acceso público."))  # fmt: skip
                if typ == "aws_s3_bucket_public_access_block" and a.get("block_public_policy") is False:
                    out.append(finding("Bloqueo de acceso público desactivado", "high", addr,
                        [("attribute", "block_public_policy = false")],
                        "Los cuatro bloqueos están en false.", "Poner los cuatro atributos en true."))  # fmt: skip
                if typ == "aws_iam_policy" and '"Action":"*"' in a.get("policy", ""):
                    out.append(finding("Política IAM con permisos totales", "high", addr,
                        [("policy", "Action = \"*\" sobre Resource = \"*\"")],
                        "La política permite cualquier acción.", "Aplicar mínimo privilegio."))  # fmt: skip
                if typ == "aws_db_instance" and a.get("publicly_accessible") is True:
                    out.append(finding("Base de datos expuesta a Internet", "critical", addr,
                        [("attribute", "publicly_accessible = true")],
                        "La instancia tiene IP pública.", "Poner publicly_accessible en false."))  # fmt: skip
                if typ == "aws_db_instance" and a.get("storage_encrypted") is False:
                    out.append(finding("RDS sin cifrado en reposo", "high", addr,
                        [("attribute", "storage_encrypted = false")],
                        "El almacenamiento no está cifrado.", "Activar storage_encrypted."))  # fmt: skip
                if typ == "aws_security_group" and "0.0.0.0/0" in json.dumps(a.get("ingress", "")):
                    out.append(finding("Security group abierto al mundo", "high", addr,
                        [("ingress", "cidr_blocks = [\"0.0.0.0/0\"] en el puerto 5432")],
                        "La base de datos acepta tráfico de cualquier origen.", "Limitar el CIDR a la red de aplicación."))  # fmt: skip
            elif agent == "cost":
                if typ == "aws_db_instance" and re.search(r"\.(8|12|16)xlarge", a.get("instance_class", "")):
                    out.append(finding("Instancia RDS sobredimensionada", "medium", addr,
                        [("attribute", f"instance_class = {a['instance_class']}")],
                        "La clase de instancia es muy grande para el entorno.", "Reducir la clase de instancia."))  # fmt: skip
        nats = [r["address"] for r in res if r["type"] == "aws_nat_gateway"]
        if agent == "cost" and len(nats) > 1:
            out.append(finding(f"{len(nats)} NAT Gateways en un entorno {env}", "medium", nats[0],
                [("topology", f"{len(nats)} aws_nat_gateway, uno por subred privada")],
                "Cada NAT tiene costo fijo mensual y por GB procesado.", "Usar un único NAT Gateway compartido.", tags=["nat"]))  # fmt: skip
        if agent == "reliability":
            for r in res:
                a = r["attributes"]
                if r["type"] == "aws_db_instance" and a.get("multi_az") is False:
                    out.append(finding("RDS en una sola zona de disponibilidad", "high", r["address"],
                        [("attribute", "multi_az = false")], "Una falla de zona tumba la base.", "Activar multi_az."))  # fmt: skip
                if r["type"] == "aws_db_instance" and a.get("backup_retention_period") == 0:
                    out.append(finding("RDS sin backups automáticos", "high", r["address"],
                        [("attribute", "backup_retention_period = 0")], "No hay puntos de recuperación.", "Definir retención de 7 días."))  # fmt: skip
        if agent == "operations":
            for r in res:
                a = r["attributes"]
                if r["type"] == "aws_db_instance" and a.get("monitoring_interval") == 0:
                    out.append(finding("RDS sin monitoreo mejorado", "low", r["address"],
                        [("attribute", "monitoring_interval = 0")], "Sin métricas de sistema operativo.", "Activar Enhanced Monitoring."))  # fmt: skip
            untagged = next(
                (r for r in res if r["type"] == "aws_s3_bucket" and "Owner" not in r["attributes"].get("tags", {})),
                None,
            )
            if untagged:
                out.append(finding("Falta el tag Owner", "low", untagged["address"],
                    [("attribute", "tags sin Owner")], "No hay responsable definido.", "Agregar el tag Owner.", tags=["governance"]))  # fmt: skip
        if self.extra_unknown_resource and agent == "security":
            out.append(finding("Hallazgo sobre un recurso inexistente", "high", "aws_lambda_function.ghost",
                [("x", "y")], "No existe en el plan.", "n/a"))  # fmt: skip
        return out

    # ---- round 2: cross-examination ---------------------------------------------------------------------------
    def _round2(self, agent: str, others: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in others:
            if agent == "reliability" and "NAT" in f["title"]:
                out.append({"target": f["id"], "stance": "disagree", "suggested_severity": "low",
                    "argument": "Colapsar a un solo NAT crea un punto único de falla: si cae su AZ, las otras dos pierden salida."})  # fmt: skip
            if agent == "cost" and f["title"].startswith("RDS en una sola zona"):
                out.append({"target": f["id"], "stance": "refine",
                    "argument": "Multi-AZ duplica el costo de una instancia ya sobredimensionada: ajustar el tamaño primero."})  # fmt: skip
            if agent == "security" and "sin backups" in f["title"]:
                out.append({"target": f["id"], "stance": "agree", "argument": "Sin backups no hay recuperación ante borrado."})  # fmt: skip
        return out

    # ---- moderator ---------------------------------------------------------------------------------------------
    def _moderate(self, user: str) -> dict[str, Any]:
        raised = self._section(user, "raised")
        challenges = self._section(user, "challenges")
        by_id = {r["id"]: r for r in raised}
        keep = raised[:-1] if self.moderator_mode == "omit_one" and raised else raised
        final = []
        for r in keep:
            final.append({**{k: r[k] for k in ("title", "severity", "resource", "evidence", "root_cause")},
                "suggested_fix": {"summary": "Aplicar la corrección propuesta.", "steps": ["Aplicar la corrección propuesta."]},
                "risk_of_fix": "low", "merged_from": [r["id"]],
                "decision": f"Se mantiene la severidad {r['severity']} según el análisis de {r['agent']}."})  # fmt: skip
        if self.moderator_mode == "hallucinate":
            final.append({"title": "Invento", "severity": "high", "resource": "aws_fake.nothing",
                "evidence": [{"kind": "x", "detail": "y"}], "root_cause": "z",
                "suggested_fix": {"summary": "s", "steps": ["s"]}, "risk_of_fix": "low",
                "merged_from": ["ZZZ-9"], "decision": "d"})  # fmt: skip
            final.append({"title": "Con id real pero recurso falso", "severity": "low", "resource": "aws_fake.nothing",
                "evidence": [{"kind": "x", "detail": "y"}], "root_cause": "z",
                "suggested_fix": {"summary": "s", "steps": ["s"]}, "risk_of_fix": "low",
                "merged_from": [raised[0]["id"]] if raised else ["ZZZ-9"], "decision": "d"})  # fmt: skip
        disagreements, accepted = [], []
        if self.moderator_mode != "omit_one":
            for c in challenges:
                if c["stance"] != "disagree":
                    continue
                target = by_id[c["target"]]
                disagreements.append({"topic": target["title"], "finding_ids": [c["target"]],
                    "positions": [{"agent": target["agent"], "stance": target["root_cause"]},
                                  {"agent": c["by"], "stance": c["argument"]}],
                    "resolution": "accepted_risk",
                    "rationale": "En un entorno dev se acepta un único NAT; en producción se conservaría uno por AZ."})  # fmt: skip
                accepted.append(
                    {"finding_ids": [c["target"]], "rationale": "Riesgo de disponibilidad aceptado en dev."}
                )
        return {"summary": f"El comité consolidó {len(final)} hallazgos.", "findings": final,
                "disagreements": disagreements, "accepted_risks": accepted}  # fmt: skip


@pytest.fixture
def committee() -> FakeCommittee:
    return FakeCommittee()
