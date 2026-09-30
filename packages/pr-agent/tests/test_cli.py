from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pr_agent import cli
from pr_agent.safety import SafeRunner

from .conftest import EXAMPLES, copy_example, load_finding

runner = CliRunner()


def copy_k8s_example(tmp_path: Path) -> Path:
    return copy_example("k8s-oom-demo", tmp_path)


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


# ---- picking one finding from an array (e.g. `kdoctor -o json`) ------------------------------------


def kdoctor_like(tmp_path: Path) -> Path:
    def f(fid: str, name: str, ns: str = "kdoctor-demo", change: str | None = None) -> dict[str, object]:
        ev = [{"kind": "pod-status", "detail": "x"}] + (
            [{"kind": "memory-limit-change", "detail": change}] if change else []
        )
        return {
            "id": fid, "schema_version": "1.0.0", "source": "kdoctor", "severity": "high", "title": f"{fid} on {name}",
            "resource": {"type": "Pod", "name": name, "namespace": ns}, "evidence": ev, "root_cause": "c",
            "suggested_fix": {"summary": "s", "steps": ["t"]}, "risk_of_fix": "low", "detected_at": "2026-09-30T12:00:00Z",
        }  # fmt: skip

    arr = [
        f("KD-K8S-001", "crashloop"),
        f("KD-K8S-002", "oom", change="container=app;from=32Mi;to=64Mi"),
        f("KD-K8S-003", "badimage"),
        f("KD-K8S-004", "pending"),
    ]
    path = tmp_path / "kdoctor.json"
    path.write_text(json.dumps(arr))
    return path


def test_the_supported_finding_is_picked_from_a_kdoctor_array(tmp_path: Path) -> None:
    repo = copy_k8s_example(tmp_path)
    src = kdoctor_like(tmp_path)
    for flags in (
        ["--supported"],
        ["--id", "KD-K8S-002"],
        ["--resource", "oom"],
        ["--resource", "kdoctor-demo/oom"],
        ["--index", "1"],
    ):
        result = runner.invoke(cli.app, ["fix", "--finding", str(src), "--repo", str(repo), "--dry-run", *flags])
        assert result.exit_code == 0, (flags, result.output)
        assert "KD-K8S-002" in result.output and '+          memory = "64Mi"' in result.output


def test_ambiguous_or_empty_selections_are_explained(tmp_path: Path) -> None:
    repo = copy_k8s_example(tmp_path)
    src = str(kdoctor_like(tmp_path))
    many = runner.invoke(cli.app, ["fix", "--finding", src, "--repo", str(repo), "--dry-run"])
    assert (
        many.exit_code == 2 and "4 findings match" in many.output and "[1] KD-K8S-002 kdoctor-demo/oom" in many.output
    )
    none = runner.invoke(cli.app, ["fix", "--finding", src, "--repo", str(repo), "--dry-run", "--id", "KD-NOPE"])
    assert none.exit_code == 2 and "no finding matches" in none.output and "KD-K8S-003" in none.output
    nofixer = runner.invoke(
        cli.app, ["fix", "--finding", src, "--repo", str(repo), "--dry-run", "--resource", "pending", "--supported"]
    )
    assert nofixer.exit_code == 2 and "no finding matches" in nofixer.output
    empty = tmp_path / "empty.json"
    empty.write_text("[]")
    assert "empty array" in runner.invoke(cli.app, ["fix", "--finding", str(empty), "--dry-run"]).output


def test_the_pipe_from_stdin_works(tmp_path: Path) -> None:
    repo = copy_k8s_example(tmp_path)
    data = kdoctor_like(tmp_path).read_text()
    result = runner.invoke(
        cli.app, ["fix", "--finding", "-", "--repo", str(repo), "--dry-run", "--supported"], input=data
    )
    assert result.exit_code == 0 and "KD-K8S-002" in result.output
