from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arch_committee import cli

from .conftest import FakeCommittee, plan_path

runner = CliRunner()
PLAN = str(plan_path("nat-per-subnet"))


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeCommittee:
    fake = FakeCommittee()
    monkeypatch.setattr(cli, "LLM_FACTORY", lambda model, region: fake)
    monkeypatch.delenv(cli.MODEL_ENV, raising=False)
    return fake


def test_version() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0 and "arch-committee" in result.output


def test_dry_run_shows_the_redacted_payload_and_calls_no_model(fake_llm: FakeCommittee, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = runner.invoke(cli.app, ["review", "--plan", PLAN, "--out", str(out), "--dry-run"])
    assert result.exit_code == 0, result.output
    for expected in (
        "Dry run",
        "no model is called",
        "round 1 · security",
        "aws_nat_gateway.per_subnet[0]",
        "Budget: 200,000",
    ):
        assert expected in result.output
    assert fake_llm.calls == [] and not out.exists()


def test_a_model_is_mandatory_for_a_real_run(fake_llm: FakeCommittee, tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["review", "--plan", PLAN, "--out", str(tmp_path)])
    assert result.exit_code == 2 and "no default" in result.output and fake_llm.calls == []


def test_a_full_run_writes_the_report_and_the_findings(fake_llm: FakeCommittee, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = runner.invoke(cli.app, ["review", "--plan", PLAN, "--out", str(out), "--model-id", "test-model"])
    assert result.exit_code == 0, result.output
    assert "Committee finished." in result.output and "Disagreements: 1" in result.output
    assert (out / "report.md").is_file()
    docs = json.loads((out / "findings.json").read_text())
    assert docs and all(d["source"] == "arch-committee" for d in docs)
    assert {c["agent"] for c in fake_llm.calls} == {"security", "cost", "reliability", "operations", "moderator"}


def test_the_model_can_come_from_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(cli.MODEL_ENV, "env-model")
    result = runner.invoke(cli.app, ["review", "--plan", PLAN, "--out", str(tmp_path / "o")])
    assert result.exit_code == 0 and "committee finished" in result.output.lower()


def test_the_output_cap_is_configurable(fake_llm: FakeCommittee, tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        ["review", "--plan", PLAN, "--out", str(tmp_path / "o"), "--model-id", "m", "--max-output-tokens", "2048"],
    )
    assert result.exit_code == 0 and result.output.count("Committee finished") == 1


def test_limits_are_passed_through(fake_llm: FakeCommittee, tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app, ["review", "--plan", PLAN, "--out", str(tmp_path / "o"), "--model-id", "m", "--max-rounds", "1"]
    )
    assert result.exit_code == 0 and "Rounds: 1" in result.output
    assert "respond_to_findings" not in {c["tool"] for c in fake_llm.calls}


def test_bad_inputs_are_usage_errors(tmp_path: Path) -> None:
    missing = runner.invoke(cli.app, ["review", "--plan", str(tmp_path / "nope.json"), "--dry-run"])
    assert missing.exit_code == 2 and "Error:" in missing.output
    bad = tmp_path / "bad.json"
    bad.write_text("{oops")
    assert runner.invoke(cli.app, ["review", "--plan", str(bad), "--dry-run"]).exit_code == 2
    notplan = tmp_path / "x.json"
    notplan.write_text("{}")
    result = runner.invoke(cli.app, ["review", "--plan", str(notplan), "--dry-run"])
    assert result.exit_code == 2 and "resource_changes" in result.output


def test_missing_aws_credentials_are_reported_clearly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def no_creds(model: str, region: str | None) -> FakeCommittee:
        raise RuntimeError("no AWS credentials available")

    monkeypatch.setattr(cli, "LLM_FACTORY", no_creds)
    result = runner.invoke(cli.app, ["review", "--plan", PLAN, "--out", str(tmp_path), "--model-id", "m"])
    assert result.exit_code == 1 and "credentials" in result.output
