from __future__ import annotations

import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from venom import __version__
from venom.cli import app

runner = CliRunner()


def test_help_lists_the_three_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("review", "fix", "doctor"):
        assert name in result.output


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and result.output.strip() == f"venom {__version__}"


def test_review_and_fix_expose_the_same_options_as_the_original_tools() -> None:
    review = runner.invoke(app, ["review", "--help"])
    fix = runner.invoke(app, ["fix", "--help"])
    assert all(opt in review.output for opt in ("--plan", "--model-id", "--max-tokens", "--max-rounds"))
    assert all(opt in fix.output for opt in ("--finding", "--dry-run", "--agent", "--supported"))


def test_review_keeps_the_no_default_model_rule(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text("{}")
    result = runner.invoke(app, ["review", "--plan", str(plan)], env={"ARCH_COMMITTEE_BEDROCK_MODEL": ""})
    assert result.exit_code == 2  # a model is required; nothing is called


def test_fix_dry_run_works_end_to_end_on_the_bundled_example() -> None:
    root = Path(__file__).resolve().parents[3]
    result = runner.invoke(
        app,
        [
            "fix",
            "--finding",
            str(root / "examples/findings/s3-no-encryption.json"),
            "--repo",
            str(root / "examples/terraform/s3-demo"),
            "--dry-run",
            "--skip-plan",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Dry-run" in result.output


def test_doctor_without_the_binary_explains_how_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    result = runner.invoke(app, ["doctor", "-n", "x"])
    assert result.exit_code == 2 and "kubectl-doctor" in result.output and "krew" in result.output


def test_doctor_passes_arguments_and_exit_code_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = tmp_path / "kubectl-doctor"
    fake.write_text('#!/bin/sh\necho "args: $*"\nexit 3\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(tmp_path))
    calls: list[list[str]] = []

    import subprocess

    real_run = subprocess.run

    def spy(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return real_run(cmd, capture_output=True, text=True, check=False)

    monkeypatch.setattr("venom.cli.subprocess.run", spy)
    result = runner.invoke(app, ["doctor", "-n", "payments", "--explain", "--explain-model", "m", "-o", "json"])
    assert result.exit_code == 3
    assert calls == [[str(fake), "-n", "payments", "--explain", "--explain-model", "m", "-o", "json"]]
