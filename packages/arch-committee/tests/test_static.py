"""Cross-check against static analysis (Checkov/Trivy JSON): findings the committee did not address become AC-STA."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from findings_schema import validate_or_raise
from typer.testing import CliRunner

from arch_committee import cli
from arch_committee.orchestrator import run_committee
from arch_committee.plan_parser import load_plan
from arch_committee.report import security_addresses, static_findings, write_outputs
from arch_committee.static import StaticFinding, StaticFindingsError, load_static, parse_static, uncovered

from .conftest import FakeCommittee, plan_path


def checkov(*checks: tuple[str, str]) -> list[dict[str, Any]]:
    failed = [{"check_id": cid, "check_name": f"name of {cid}", "resource": res} for cid, res in checks]
    return [{"check_type": "terraform_plan", "results": {"failed_checks": failed, "passed_checks": []}}]


def test_parses_checkov_and_trivy() -> None:
    assert parse_static(checkov(("CKV_X", "aws_s3_bucket.a"))) == [
        StaticFinding("checkov", "CKV_X", "aws_s3_bucket.a", "name of CKV_X")
    ]
    trivy = {
        "Results": [
            {
                "Misconfigurations": [
                    {"ID": "AVD-1", "Title": "t", "Status": "FAIL", "CauseMetadata": {"Resource": "aws_s3_bucket.a"}},
                    {"ID": "AVD-2", "Title": "t", "Status": "PASS", "CauseMetadata": {"Resource": "aws_s3_bucket.a"}},
                ]
            }
        ]
    }
    assert [f.rule for f in parse_static(trivy)] == ["AVD-1"]
    with pytest.raises(StaticFindingsError):
        parse_static({"something": "else"})


def test_uncovered_uses_base_addresses_and_deduplicates() -> None:
    static = [
        StaticFinding("checkov", "R1", "aws_nat_gateway.x[0]", ""),
        StaticFinding("checkov", "R1", "aws_nat_gateway.x[1]", ""),
        StaticFinding("trivy", "R2", "aws_s3_bucket.a", ""),
    ]
    assert [f.rule for f in uncovered(static, {"aws_s3_bucket.a"})] == ["R1"]
    assert uncovered(static, {"aws_nat_gateway.x[2]", "aws_s3_bucket.a"}) == []


def test_only_resources_the_committee_did_not_address_become_findings(committee: FakeCommittee, tmp_path: Path) -> None:
    plan = load_plan(plan_path("public-bucket"))
    result = run_committee(plan, committee)
    covered = security_addresses(result)
    assert covered, "the fake security specialist reports something on this plan"
    missed = next(r.address for r in plan.resources if r.address not in covered)
    static = [
        StaticFinding("checkov", "CKV_COVERED", sorted(covered)[0], "covered by the committee"),
        StaticFinding("checkov", "CKV_MISSED", missed, "missed by the committee"),
    ]
    sta = static_findings(result, static)
    assert [(f.id, f"{f.resource.type}.{f.resource.name}") for f in sta] == [("AC-STA-001", missed)]
    report, findings_path, _ = write_outputs(result, tmp_path, "m", static)
    docs = json.loads(findings_path.read_text(encoding="utf-8"))
    for d in docs:
        validate_or_raise(d)
    assert [d["id"] for d in docs if d["id"].startswith("AC-STA")] == ["AC-STA-001"]
    text = report.read_text(encoding="utf-8")
    assert "## Contraste con análisis estático" in text and "CKV_MISSED" in text and "CKV_COVERED" not in text


def test_without_static_input_there_is_no_section(committee: FakeCommittee, tmp_path: Path) -> None:
    result = run_committee(load_plan(plan_path("public-bucket")), committee)
    report, _, _ = write_outputs(result, tmp_path, "m")
    assert "Contraste con análisis estático" not in report.read_text(encoding="utf-8")


def test_cli_reads_static_findings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "LLM_FACTORY", lambda model, region: FakeCommittee())
    static = tmp_path / "checkov.json"
    static.write_text(json.dumps(checkov(("CKV_AWS_999", "aws_iam_policy.deploy"))), encoding="utf-8")
    out = tmp_path / "out"
    args = ["review", "--plan", str(plan_path("public-bucket")), "--out", str(out), "--model-id", "m"]
    result = CliRunner().invoke(cli.app, [*args, "--static-findings", str(static)])
    assert result.exit_code == 0, result.output
    assert "Contraste con análisis estático" in (out / "report.md").read_text(encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    result = CliRunner().invoke(cli.app, [*args, "--static-findings", str(bad)])
    assert result.exit_code == 2 and "Checkov" in result.output
    assert load_static([]) == []
