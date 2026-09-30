"""Committee output: ``findings.json`` (valid against findings-schema) and ``report.md`` (Spanish, for humans)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from findings_schema import Finding, FindingValidationError, validate_or_raise

from . import __version__
from .agents import SPECIALISTS, prompt_versions
from .models import FinalFinding
from .orchestrator import SEVERITY_RANK, CommitteeResult
from .plan_parser import PlannedResource

AREA_NAME = {a.code: a.name for a in SPECIALISTS}
SCHEMA_VERSION = "1.0.0"


# ---- findings.json ----------------------------------------------------------------------------------------------


def _resolve(result: CommitteeResult, address: str) -> PlannedResource | None:
    return next((r for r in result.plan.resources if r.address == address), None) or next(
        (r for r in result.plan.resources if r.base_address == address), None
    )


def _area_code(result: CommitteeResult, f: FinalFinding) -> str:
    by_id = {r.fid: r for r in result.raised}
    first = by_id.get(f.merged_from[0])
    return next((a.code for a in SPECIALISTS if first and a.name == first.agent), "OPS")


def to_findings(result: CommitteeResult) -> tuple[list[tuple[Finding, FinalFinding]], list[str]]:
    """Map the committee's final findings to the common schema, paired with their source.

    Findings that do not satisfy the contract are skipped and reported as warnings.
    """
    counters: Counter[str] = Counter()
    by_id = {r.fid: r for r in result.raised}
    detected = result.generated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    pairs: list[tuple[Finding, FinalFinding]] = []
    warnings: list[str] = []

    for f in result.final:
        code = _area_code(result, f)
        counters[code] += 1
        fid = f"AC-{code}-{counters[code]:03d}"

        if f.resource == "plan":
            resource = {"type": "terraform_plan", "name": "plan"}
        else:
            pr = _resolve(result, f.resource)
            resource = (
                {"type": pr.type, "name": pr.name + (f"[{pr.index}]" if pr.index is not None else "")}
                if pr
                else {"type": f.resource.split(".")[0], "name": f.resource.split(".", 1)[-1]}
            )

        evidence = [{"kind": e.kind, "detail": e.detail} for e in f.evidence]
        for c in result.challenges:
            if c.challenge.target in f.merged_from:
                evidence.append({"kind": "debate", "detail": f"{c.by} ({c.challenge.stance}): {c.challenge.argument}"})
        evidence.append({"kind": "committee-decision", "detail": f.decision})

        agents = sorted({by_id[i].agent for i in f.merged_from if i in by_id})
        tags = list(dict.fromkeys(["arch-committee", AREA_NAME[code], *agents, *[t for t in f.tags if t.strip()]]))
        fix: dict[str, Any] = {"summary": f.suggested_fix.summary, "steps": f.suggested_fix.steps}
        if f.suggested_fix.iac_hint:
            fix["iac_hint"] = f.suggested_fix.iac_hint

        data = {
            "id": fid,
            "schema_version": SCHEMA_VERSION,
            "source": "arch-committee",
            "severity": f.severity,
            "title": f.title[:200],
            "resource": resource,
            "evidence": evidence,
            "root_cause": f.root_cause,
            "suggested_fix": fix,
            "risk_of_fix": f.risk_of_fix,
            "tags": tags,
            "detected_at": detected,
        }
        try:
            pairs.append((Finding.from_dict(data), f))
        except FindingValidationError as exc:
            warnings.append(f"{fid} «{f.title}» no cumple findings-schema y se omitió: {exc.issues[0]}")
    return pairs, warnings


def findings_json(findings: list[Finding]) -> str:
    docs = [f.to_dict() for f in findings]
    for d in docs:
        validate_or_raise(d)  # belt and braces: never write a document the contract rejects
    return json.dumps(docs, ensure_ascii=False, indent=2) + "\n"


# ---- report.md --------------------------------------------------------------------------------------------------


def _cell(text: str, limit: int = 160) -> str:
    text = " ".join(str(text).split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def render_report(
    result: CommitteeResult, pairs: list[tuple[Finding, FinalFinding]], model_id: str, warnings: list[str]
) -> str:
    plan = result.plan
    findings = [fin for fin, _ in pairs]
    sev = Counter(f.severity.value for f in findings)
    by_id = {r.fid: r for r in result.raised}
    out: list[str] = []
    add = out.append

    add("# Informe del comité de arquitectura")
    add("")
    add(f"_Generado el {result.generated_at.strftime('%Y-%m-%d %H:%M UTC')} por **arch-committee** {__version__}._")
    add("")
    add("## Resumen")
    add("")
    add(result.summary.strip() or "Sin resumen.")
    add("")
    counts = ", ".join(f"{sev[s]} {s}" for s in ("critical", "high", "medium", "low", "info") if sev[s]) or "ninguno"
    add(f"- **Hallazgos finales:** {len(findings)} ({counts})")
    add(f"- **Desacuerdos registrados:** {len(result.disagreements)}")
    add(f"- **Riesgos aceptados:** {len(result.accepted_risks)}")
    add("")

    add("## Plan revisado")
    add("")
    acts = ", ".join(f"{n} {a}" for a, n in plan.action_counts.items())
    add(f"- Terraform {plan.terraform_version}; {len(plan.resources)} recursos ({acts}).")
    add("- Tipos: " + ", ".join(f"`{t}` ×{n}" for t, n in plan.type_counts.items()))
    add("- Los valores sensibles y los secretos se enmascararon **antes** de enviar el plan al modelo.")
    add("")

    add("## Decisiones")
    add("")
    if findings:
        rows = []
        for fin, final in pairs:
            origin = ", ".join(final.merged_from)
            rows.append([fin.id, fin.severity.value, f"`{final.resource}`", fin.title, origin, final.decision])
        add(_table(["ID", "Severidad", "Recurso", "Hallazgo", "Origen", "Decisión del moderador"], rows))
    else:
        add("El comité no dejó hallazgos.")
    add("")

    add("## Desacuerdos explícitos")
    add("")
    if result.disagreements:
        label = {
            "resolved": "Resuelto",
            "accepted_risk": "Riesgo aceptado",
            "unresolved": "Sin resolver (requiere decisión humana)",
        }
        for i, d in enumerate(result.disagreements, 1):
            add(f"### {i}. {d.topic}")
            add("")
            if d.finding_ids:
                add(f"- **Hallazgos:** {', '.join(d.finding_ids)}")
            for p in d.positions:
                add(f"- **{p.agent}:** {p.stance}")
            add(f"- **Resolución:** {label.get(d.resolution, d.resolution)}")
            add(f"- **Razón:** {d.rationale}")
            add("")
    else:
        add("No se registraron desacuerdos entre los agentes.")
        add("")

    add("## Riesgos aceptados")
    add("")
    if result.accepted_risks:
        for ar in result.accepted_risks:
            ids = ", ".join(ar.finding_ids) or "—"
            add(f"- **{ids}:** {ar.rationale}")
    else:
        add("Ninguno.")
    add("")

    add("## Detalle de los hallazgos")
    add("")
    for fin, final in pairs:
        add(f"### {fin.id} · {fin.severity.value.upper()} · {fin.title}")
        add("")
        add(f"- **Recurso:** `{final.resource}`")
        add(f"- **Causa probable:** {fin.root_cause}")
        add("- **Evidencia:**")
        for e in fin.evidence:
            add(f"  - _{e.kind}_: {e.detail}")
        add(f"- **Cómo arreglarlo** (riesgo {fin.risk_of_fix.value}): {fin.suggested_fix.summary}")
        for n, step in enumerate(fin.suggested_fix.steps, 1):
            add(f"  {n}. {step}")
        if fin.suggested_fix.iac_hint:
            add(f"  - IaC: `{fin.suggested_fix.iac_hint}`")
        add("")

    add("## Qué reportó cada especialista (ronda 1)")
    add("")
    for spec in SPECIALISTS:
        mine = [r for r in result.raised if r.agent == spec.name]
        add(f"**{spec.name}** — {len(mine)} hallazgos")
        for r in mine:
            add(f"- `{r.fid}` [{r.finding.severity}] {r.finding.title} (`{r.finding.resource}`)")
        add("")
    if result.challenges:
        add("### Réplicas (ronda 2)")
        add("")
        rows = [
            [c.by, c.challenge.target, f"{by_id[c.challenge.target].agent}", c.challenge.stance, c.challenge.argument]
            for c in result.challenges
        ]
        add(_table(["De", "Sobre", "Agente", "Postura", "Argumento"], rows))
        add("")

    moderator = "sí" if result.moderator_ran else "no (consolidación automática)"
    add("## Ejecución")
    add("")
    add(f"- **Modelo:** `{model_id}`")
    add(f"- **Rondas ejecutadas:** {result.rounds_run}; moderador: {moderator}")
    add(f"- **Tokens:** {result.tokens_used:,} de un máximo de {result.tokens_max:,}")
    add("- **Versiones de los prompts:** " + ", ".join(f"{k} {v}" for k, v in prompt_versions().items()))
    notes = [*result.notes, *warnings]
    if notes:
        add("")
        add("### Avisos")
        add("")
        for note in notes:
            add(f"- {note}")
    add("")
    add("_Este informe es una ayuda a la decisión: lo debe revisar una persona. El comité no aplica ningún cambio._")
    add("")
    return "\n".join(out)


def write_outputs(result: CommitteeResult, out_dir: Path, model_id: str) -> tuple[Path, Path, list[str]]:
    pairs, warnings = to_findings(result)
    out_dir.mkdir(parents=True, exist_ok=True)
    findings_path = out_dir / "findings.json"
    report_path = out_dir / "report.md"
    findings_path.write_text(findings_json([fin for fin, _ in pairs]), encoding="utf-8")
    report_path.write_text(render_report(result, pairs, model_id, warnings), encoding="utf-8")
    return report_path, findings_path, warnings


def sort_final(final: list[FinalFinding]) -> list[FinalFinding]:
    return sorted(final, key=lambda f: SEVERITY_RANK[f.severity])
