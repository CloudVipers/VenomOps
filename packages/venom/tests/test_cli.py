from __future__ import annotations

import stat
from collections.abc import Sequence
from pathlib import Path

import pytest
import typer
from pr_agent import cli as pr_agent_cli
from pr_agent.safety import CommandResult, SafeRunner, check_command
from typer.testing import CliRunner

from venom import __version__
from venom.cli import app

runner = CliRunner()


class NoTerraformRunner(SafeRunner):
    """Keeps the real allowlist but never spawns terraform (CI has none and would need the AWS provider)."""

    def run(self, argv: Sequence[str]) -> CommandResult:
        check_command(argv)
        if argv[0] == "terraform":
            return CommandResult(tuple(argv), 0, "Success!\n", "")
        return super().run(argv)


def test_help_lists_the_three_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("review", "fix", "doctor"):
        assert name in result.output


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and result.output.strip() == f"venom {__version__}"


def _options(command: str) -> set[str]:
    """Option names of a registered command, read from the click model (not from rendered, colorized help)."""
    cmd = typer.main.get_command(app).commands[command]  # type: ignore[attr-defined]
    return {opt for param in cmd.params for opt in param.opts}


def test_review_and_fix_expose_the_same_options_as_the_original_tools() -> None:
    assert {"--plan", "--model-id", "--max-tokens", "--max-rounds"} <= _options("review")
    assert {"--finding", "--dry-run", "--agent", "--supported"} <= _options("fix")


def test_review_keeps_the_no_default_model_rule(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text("{}")
    result = runner.invoke(app, ["review", "--plan", str(plan)], env={"ARCH_COMMITTEE_BEDROCK_MODEL": ""})
    assert result.exit_code == 2  # a model is required; nothing is called


def test_fix_dry_run_works_end_to_end_on_the_bundled_example(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr_agent_cli, "RUNNER_FACTORY", NoTerraformRunner)
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
    assert result.exit_code == 2 and "kubectl-venom_doctor" in result.output and "krew" in result.output


def test_doctor_passes_arguments_and_exit_code_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = tmp_path / "kubectl-venom_doctor"
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
