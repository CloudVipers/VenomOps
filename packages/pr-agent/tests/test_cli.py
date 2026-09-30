from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pr_agent import cli
from pr_agent.safety import SafeRunner

from .conftest import EXAMPLES, load_finding

runner = CliRunner()
S3 = str(EXAMPLES / "findings" / "s3-no-encryption.json")


@pytest.fixture(autouse=True)
def fake_terraform(monkeypatch: pytest.MonkeyPatch, runner_factory: Callable[[Path], SafeRunner]) -> None:
    monkeypatch.setattr(cli, "RUNNER_FACTORY", runner_factory)


def test_version() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0 and "pr-agent" in result.output


def test_dry_run_prints_the_diff_and_the_pr(s3_repo: Path) -> None:
    result = runner.invoke(cli.app, ["fix", "--finding", S3, "--repo", str(s3_repo), "--dry-run"])
    assert result.exit_code == 0, result.output
    for expected in (
        "TF-S3-001",
        "+resource",
        "--- PR body ---",
        "Checklist de revisión humana",
        "nothing was written",
    ):
        assert expected in result.output
    assert "server_side_encryption" not in (s3_repo / "main.tf").read_text()


def test_array_input_needs_an_index_and_stdin_is_supported(s3_repo: Path, tmp_path: Path) -> None:
    findings = [json.loads(Path(S3).read_text()), json.loads((EXAMPLES / "findings/ebs-gp2.json").read_text())]
    many = tmp_path / "many.json"
    many.write_text(json.dumps(findings))

    result = runner.invoke(cli.app, ["fix", "--finding", str(many), "--repo", str(s3_repo), "--dry-run"])
    assert result.exit_code == 2 and "--index" in result.output and "TF-EBS-001" in result.output
    result = runner.invoke(
        cli.app, ["fix", "--finding", str(many), "--repo", str(s3_repo), "--dry-run", "--index", "5"]
    )
    assert result.exit_code == 2 and "out of range" in result.output

    result = runner.invoke(
        cli.app, ["fix", "--finding", "-", "--repo", str(s3_repo), "--dry-run"], input=json.dumps(findings[:1])
    )
    assert result.exit_code == 0 and "TF-S3-001" in result.output


def test_invalid_finding_is_reported_with_the_schema_errors(s3_repo: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"id": "x", "severity": "urgent"}))
    result = runner.invoke(cli.app, ["fix", "--finding", str(bad), "--repo", str(s3_repo), "--dry-run"])
    assert result.exit_code == 2 and "findings-schema" in result.output

    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    result = runner.invoke(cli.app, ["fix", "--finding", str(broken), "--repo", str(s3_repo), "--dry-run"])
    assert result.exit_code == 2 and "not valid JSON" in result.output


def test_unsupported_finding_is_aborted_with_a_helpful_message(s3_repo: Path, tmp_path: Path) -> None:
    data = json.loads(Path(S3).read_text())
    data["id"] = "KD-K8S-001"
    f = tmp_path / "k8s.json"
    f.write_text(json.dumps(data))
    result = runner.invoke(cli.app, ["fix", "--finding", str(f), "--repo", str(s3_repo), "--dry-run"])
    assert result.exit_code == 1 and "no fixer" in result.output and "--agent" in result.output


def test_nothing_to_do_exits_zero(s3_repo: Path) -> None:
    first = runner.invoke(cli.app, ["fix", "--finding", S3, "--repo", str(s3_repo), "--dry-run"])
    assert first.exit_code == 0
    from pr_agent.workflow import FixOptions, run_fix

    report = run_fix(
        load_finding("s3-no-encryption.json"), FixOptions(repo=s3_repo, dry_run=True), runner_factory=cli.RUNNER_FACTORY
    )
    (s3_repo / "main.tf").write_text(report.changed_files["main.tf"])
    again = runner.invoke(cli.app, ["fix", "--finding", S3, "--repo", str(s3_repo), "--dry-run"])
    assert again.exit_code == 0 and "Nothing to do" in again.output


def test_agent_requires_an_explicit_model(s3_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cli.MODEL_ENV, raising=False)
    result = runner.invoke(cli.app, ["fix", "--finding", S3, "--repo", str(s3_repo), "--dry-run", "--agent"])
    assert result.exit_code == 2 and "no default" in result.output


def test_without_dry_run_a_github_token_is_required(s3_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(cli, "GITHUB_FACTORY", cli._github)  # noqa: SLF001 - the real factory
    result = runner.invoke(cli.app, ["fix", "--finding", S3, "--repo", str(s3_repo)])
    assert result.exit_code == 1 and "GITHUB_TOKEN" in result.output
    assert "server_side_encryption" not in (s3_repo / "main.tf").read_text()


def test_real_publish_path_through_the_cli(git_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_workflow import FakeGitHub

    work, remote = git_repo
    gh = FakeGitHub()
    monkeypatch.setattr(cli, "GITHUB_FACTORY", lambda: gh)
    result = runner.invoke(cli.app, ["fix", "--finding", S3, "--repo", str(work), "--github-repo", "acme/infra"])
    assert result.exit_code == 0, result.output
    assert "Pull request opened" in result.output and "never merged" in result.output
    assert gh.opened and gh.opened[0]["head"] == "fix/tf-s3-001-demo-logs"
