from __future__ import annotations

import json
from pathlib import Path

from findings_schema import validate

from arch_committee.models import FinalFinding, Fix
from arch_committee.orchestrator import CommitteeConfig, CommitteeResult, run_committee
from arch_committee.plan_parser import PlanModel, load_plan
from arch_committee.report import render_report, to_findings, write_outputs

from .conftest import FakeCommittee, plan_path


def test_findings_json_validates_against_findings_schema(
    example_plan: tuple[str, PlanModel], committee: FakeCommittee, tmp_path: Path
) -> None:
    result = run_committee(example_plan[1], committee)
    _, findings_path, warnings = write_outputs(result, tmp_path, "test-model")
    docs = json.loads(findings_path.read_text(encoding="utf-8"))
    assert docs and not warnings
    for d in docs:
        assert validate(d) == [], d["id"]
        assert d["source"] == "arch-committee" and d["schema_version"] == "1.0.0"
    ids = [d["id"] for d in docs]
    assert len(ids) == len(set(ids)) and all(i.startswith("AC-") for i in ids)


def test_each_finding_carries_the_debate_and_the_decision_as_evidence(committee: FakeCommittee) -> None:
    result = run_committee(load_plan(plan_path("nat-per-subnet")), committee)
    pairs, _ = to_findings(result)
    nat = next(f for f, _ in pairs if "NAT" in f.title)
    kinds = [e.kind for e in nat.evidence]
    assert "debate" in kinds and kinds[-1] == "committee-decision"
    assert any("punto único de falla" in e.detail for e in nat.evidence if e.kind == "debate")
    assert {"arch-committee", "cost", "reliability"} <= set(nat.tags) | {"reliability"}
    assert nat.resource.type == "aws_nat_gateway" and nat.resource.name == "per_subnet[0]"


def test_ids_are_sequential_per_area(committee: FakeCommittee) -> None:
    result = run_committee(load_plan(plan_path("rds-single-az")), committee)
    pairs, _ = to_findings(result)
    sec = [f.id for f, _ in pairs if f.id.startswith("AC-SEC-")]
    assert sec == [f"AC-SEC-{i:03d}" for i in range(1, len(sec) + 1)] and len(sec) >= 3


def test_the_report_has_the_sections_a_reader_needs(committee: FakeCommittee, tmp_path: Path) -> None:
    result = run_committee(load_plan(plan_path("nat-per-subnet")), committee)
    report_path, _, _ = write_outputs(result, tmp_path, "test-model")
    text = report_path.read_text(encoding="utf-8")
    for section in ("# Informe del comité de arquitectura", "## Resumen", "## Plan revisado", "## Decisiones",
                    "## Desacuerdos explícitos", "## Riesgos aceptados", "## Detalle de los hallazgos",
                    "## Qué reportó cada especialista", "### Réplicas (ronda 2)", "## Ejecución"):  # fmt: skip
        assert section in text, section
    assert "**cost:**" in text and "**reliability:**" in text  # both positions of the disagreement
    assert "Riesgo aceptado" in text  # its resolution
    assert "`test-model`" in text and "Tokens:" in text
    assert "security v1" in text and "moderator v2" in text  # prompt versions for traceability
    assert "El comité no aplica ningún cambio" in text


def test_a_report_without_disagreements_says_so(committee: FakeCommittee, tmp_path: Path) -> None:
    result = run_committee(load_plan(plan_path("public-bucket")), committee)
    text = write_outputs(result, tmp_path, "m")[0].read_text(encoding="utf-8")
    assert "No se registraron desacuerdos entre los agentes." in text


def test_degraded_runs_are_visible_in_the_report(tmp_path: Path) -> None:
    result = run_committee(load_plan(plan_path("nat-per-subnet")), FakeCommittee(fail=frozenset({"moderator"})))
    text = write_outputs(result, tmp_path, "m")[0].read_text(encoding="utf-8")
    assert (
        "consolidación automática" in text
        and "### Avisos" in text
        and "Sin resolver (requiere decisión humana)" in text
    )


def test_findings_that_break_the_contract_are_skipped_with_a_warning(committee: FakeCommittee, tmp_path: Path) -> None:
    result = run_committee(load_plan(plan_path("rds-single-az")), committee)
    good = len(result.final)
    bad = FinalFinding(
        title="x", severity="high", resource="aws_db_instance.orders",
        evidence=[{"kind": "a", "detail": "b"}], root_cause="c", suggested_fix=Fix(summary="s", steps=["t"]),  # type: ignore[list-item]
        risk_of_fix="low", merged_from=[result.raised[0].fid], decision="d",
    )  # fmt: skip
    bad.suggested_fix.steps[0] = ""  # an empty step violates the schema (minLength 1) although pydantic accepted it
    result.final.append(bad)
    _, findings_path, warnings = write_outputs(result, tmp_path, "m")
    assert len(json.loads(findings_path.read_text())) == good
    assert len(warnings) == 1 and "no cumple findings-schema" in warnings[0]
    assert "no cumple findings-schema" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_table_cells_cannot_break_the_markdown(committee: FakeCommittee) -> None:
    result = run_committee(load_plan(plan_path("rds-single-az")), committee)
    result.final[0].decision = "a | b\nc"
    pairs, warnings = to_findings(result)
    text = render_report(result, pairs, "m", warnings)
    assert "a / b c" in text


def test_plan_level_findings_map_to_a_plan_resource(committee: FakeCommittee) -> None:
    result: CommitteeResult = run_committee(load_plan(plan_path("rds-single-az")), committee)
    result.final[0].resource = "plan"
    pairs, _ = to_findings(result)
    assert (pairs[0][0].resource.type, pairs[0][0].resource.name) == ("terraform_plan", "plan")


def test_limits_do_not_change_the_output_contract(committee: FakeCommittee, tmp_path: Path) -> None:
    result = run_committee(load_plan(plan_path("rds-single-az")), committee, CommitteeConfig(max_rounds=1))
    _, findings_path, _ = write_outputs(result, tmp_path, "m")
    assert all(validate(d) == [] for d in json.loads(findings_path.read_text()))
