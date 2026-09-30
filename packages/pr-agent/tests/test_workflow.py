from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import pytest
from findings_schema import Finding

from pr_agent import hcl
from pr_agent.fixes import FixOutcome
from pr_agent.github_client import PullRequestClient
from pr_agent.safety import SafeRunner
from pr_agent.tools import ToolBox
from pr_agent.workflow import FixAborted, FixOptions, NothingToDo, branch_name, run_fix, snapshot

from .conftest import FakeTerraformRunner, git, load_finding, make_finding

Factory = Callable[[Path], SafeRunner]


class FakeGitHub:
    """Records what would be sent to GitHub. It has no merge capability, like the real client."""

    opened: ClassVar[list[dict[str, str]]]

    def __init__(self) -> None:
        self.opened = []

    def default_branch(self, slug: str) -> str:
        return "main"

    def open_pull_request(self, slug: str, *, title: str, body: str, head: str, base: str) -> str:
        self.opened.append({"slug": slug, "title": title, "body": body, "head": head, "base": base})
        return f"https://github.com/{slug}/pull/1"


def test_fake_github_matches_the_client_protocol() -> None:
    client: PullRequestClient = FakeGitHub()
    assert client.default_branch("o/r") == "main"


# ---- dry-run ---------------------------------------------------------------------------------


def test_dry_run_builds_diff_and_body_without_touching_the_repo(
    s3_repo: Path, runner_factory: Factory, tf_calls: list[list[str]]
) -> None:
    before = snapshot(s3_repo)
    report = run_fix(
        load_finding("s3-no-encryption.json"), FixOptions(repo=s3_repo, dry_run=True), runner_factory=runner_factory
    )

    assert snapshot(s3_repo) == before  # the real repository is exactly as it was
    assert not (s3_repo / ".terraform").exists() and not (s3_repo / ".terraform.lock.hcl").exists()
    assert report.dry_run and report.pr_url is None
    assert report.branch == "fix/tf-s3-001-demo-logs"
    assert list(report.changed_files) == ["main.tf"]
    assert '+resource "aws_s3_bucket_server_side_encryption_configuration" "demo_logs"' in report.diff
    removed = [ln for ln in report.diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert removed == []  # the fix only adds lines
    hcl.parse(report.changed_files["main.tf"])

    # terraform was used only in the allowed, read-only ways (init without backend, validate, plan)
    assert [c[1] for c in tf_calls] == ["init", "validate", "plan"]
    assert tf_calls[0] == ["terraform", "init", "-backend=false", "-input=false", "-no-color"]
    assert not any(c[1] in {"apply", "destroy"} for c in tf_calls)


def test_pr_body_contains_the_sections_a_reviewer_needs(s3_repo: Path, runner_factory: Factory) -> None:
    report = run_fix(
        load_finding("s3-no-encryption.json"), FixOptions(repo=s3_repo, dry_run=True), runner_factory=runner_factory
    )
    body = report.body
    for expected in (
        "TF-S3-001",
        "Bucket S3 sin cifrado",
        "```diff",
        "terraform validate",
        "Plan: 1 to add",
        "Nivel de riesgo",
        "Checklist de revisión humana",
        "- [ ]",
        "aprobación humana",
    ):
        assert expected.lower() in body.lower(), expected
    assert report.title.startswith("fix(terraform):") and "[TF-S3-001]" in report.title


def test_plan_failure_is_not_fatal_by_default_but_is_disclosed(s3_repo: Path, tf_calls: list[list[str]]) -> None:
    factory: Factory = lambda cwd: FakeTerraformRunner(cwd, tf_calls, plan_ok=False)  # noqa: E731
    report = run_fix(
        load_finding("s3-no-encryption.json"), FixOptions(repo=s3_repo, dry_run=True), runner_factory=factory
    )
    assert report.plan is not None and not report.plan.ok
    assert "no disponible" in report.body

    with pytest.raises(FixAborted, match="terraform plan failed"):
        run_fix(
            load_finding("s3-no-encryption.json"),
            FixOptions(repo=s3_repo, dry_run=True, require_plan=True),
            runner_factory=factory,
        )


def test_skip_plan_never_calls_plan(s3_repo: Path, runner_factory: Factory, tf_calls: list[list[str]]) -> None:
    run_fix(
        load_finding("s3-no-encryption.json"),
        FixOptions(repo=s3_repo, dry_run=True, skip_plan=True),
        runner_factory=runner_factory,
    )
    assert "plan" not in [c[1] for c in tf_calls]


def test_validate_failure_aborts_and_leaves_the_repo_untouched(s3_repo: Path, tf_calls: list[list[str]]) -> None:
    before = snapshot(s3_repo)
    factory: Factory = lambda cwd: FakeTerraformRunner(cwd, tf_calls, validate_ok=False)  # noqa: E731
    with pytest.raises(FixAborted, match="terraform validate failed"):
        run_fix(load_finding("s3-no-encryption.json"), FixOptions(repo=s3_repo, dry_run=True), runner_factory=factory)
    assert snapshot(s3_repo) == before
    assert "plan" not in [c[1] for c in tf_calls]  # never plan a configuration that does not validate


def test_nothing_to_do_when_already_fixed(s3_repo: Path, runner_factory: Factory) -> None:
    finding = load_finding("s3-no-encryption.json")
    report = run_fix(finding, FixOptions(repo=s3_repo, dry_run=True), runner_factory=runner_factory)
    (s3_repo / "main.tf").write_text(report.changed_files["main.tf"])
    with pytest.raises(NothingToDo):
        run_fix(finding, FixOptions(repo=s3_repo, dry_run=True), runner_factory=runner_factory)


def test_unknown_finding_without_agent_is_refused(s3_repo: Path, runner_factory: Factory) -> None:
    finding = make_finding(id="KD-K8S-001")
    with pytest.raises(FixAborted, match="no fixer"):
        run_fix(finding, FixOptions(repo=s3_repo, dry_run=True), runner_factory=runner_factory)


def test_missing_repo_directory(tmp_path: Path, runner_factory: Factory) -> None:
    with pytest.raises(FixAborted, match="not found"):
        run_fix(
            load_finding("s3-no-encryption.json"),
            FixOptions(repo=tmp_path / "nope", dry_run=True),
            runner_factory=runner_factory,
        )


# ---- minimal-diff enforcement -----------------------------------------------------------------


def _agent(edit: Callable[[ToolBox], None], files: set[str]) -> Callable[[Finding, ToolBox], FixOutcome]:
    def run(_finding: Finding, tools: ToolBox) -> FixOutcome:
        edit(tools)
        return FixOutcome(summary="Cambio de prueba.", files=frozenset(files))

    return run


def _touch(tools: ToolBox, name: str) -> None:
    tools.edit_hcl(name, {"op": "append_block", "content": 'resource "aws_s3_bucket" "extra" {\n  bucket = "x"\n}'})


def test_a_change_to_an_undeclared_file_aborts(s3_repo: Path, runner_factory: Factory) -> None:
    finding = make_finding(id="CUSTOM-001")
    agent = _agent(lambda t: _touch(t, "versions.tf"), {"main.tf"})  # declares main.tf but edits versions.tf
    with pytest.raises(FixAborted, match="did not declare"):
        run_fix(finding, FixOptions(repo=s3_repo, dry_run=True), runner_factory=runner_factory, agent=agent)


def test_created_or_deleted_files_abort(s3_repo: Path, runner_factory: Factory) -> None:
    finding = make_finding(id="CUSTOM-001")
    agent = _agent(lambda t: (t.root / "extra.tf").write_text("# new\n"), {"main.tf"})
    with pytest.raises(FixAborted, match="created/removed"):
        run_fix(finding, FixOptions(repo=s3_repo, dry_run=True), runner_factory=runner_factory, agent=agent)
    agent = _agent(lambda t: (t.root / "versions.tf").unlink(), {"versions.tf"})
    with pytest.raises(FixAborted, match="created/removed"):
        run_fix(finding, FixOptions(repo=s3_repo, dry_run=True), runner_factory=runner_factory, agent=agent)


def test_an_oversized_diff_aborts(s3_repo: Path, runner_factory: Factory) -> None:
    finding = make_finding(id="CUSTOM-001")
    big = "\n".join(f'resource "aws_s3_bucket" "b{i}" {{\n  bucket = "b{i}"\n}}\n' for i in range(40))

    def edit(tools: ToolBox) -> None:
        tools.edit_hcl("main.tf", {"op": "append_block", "content": big})

    with pytest.raises(FixAborted, match="too large"):
        run_fix(
            finding,
            FixOptions(repo=s3_repo, dry_run=True),
            runner_factory=runner_factory,
            agent=_agent(edit, {"main.tf"}),
        )


def test_no_change_aborts(s3_repo: Path, runner_factory: Factory) -> None:
    finding = make_finding(id="CUSTOM-001")
    with pytest.raises(FixAborted, match="no change"):
        run_fix(
            finding,
            FixOptions(repo=s3_repo, dry_run=True),
            runner_factory=runner_factory,
            agent=_agent(lambda t: None, {"main.tf"}),
        )


def test_branch_name_is_safe_and_matches_the_git_allowlist() -> None:
    assert (
        branch_name(make_finding(id="TF-S3-001", resource={"type": "aws_s3_bucket", "name": "My_Bucket!!"}))
        == "fix/tf-s3-001-my-bucket"
    )


# ---- real publishing (git + fake GitHub) ------------------------------------------------------


def test_publish_creates_branch_commit_push_and_pr_then_restores_the_user_branch(
    git_repo: tuple[Path, Path], runner_factory: Factory
) -> None:
    work, remote = git_repo
    gh = FakeGitHub()
    report = run_fix(
        load_finding("s3-no-encryption.json"),
        FixOptions(repo=work, github_repo="acme/infra"),
        runner_factory=runner_factory,
        github=gh,
    )
    assert report.pr_url == "https://github.com/acme/infra/pull/1"
    pr = gh.opened[0]
    assert pr["head"] == "fix/tf-s3-001-demo-logs" and pr["base"] == "main" and pr["slug"] == "acme/infra"
    assert "Checklist de revisión humana" in pr["body"]

    # the branch (and only the branch) reached the remote; main was never pushed to
    assert git(remote, "rev-parse", "fix/tf-s3-001-demo-logs")
    assert git(remote, "rev-parse", "main") == git(work, "rev-parse", "main")
    assert git(work, "log", "-1", "--format=%s", "fix/tf-s3-001-demo-logs").startswith("fix(terraform):")
    assert git(work, "diff", "--name-only", "main", "fix/tf-s3-001-demo-logs") == "main.tf"
    # the user's branch is checked out again with a clean tree
    assert git(work, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(work, "status", "--porcelain") == ""


def test_publish_refuses_a_dirty_working_tree(git_repo: tuple[Path, Path], runner_factory: Factory) -> None:
    work, _ = git_repo
    (work / "main.tf").write_text((work / "main.tf").read_text() + "\n# local edit\n")
    with pytest.raises(FixAborted, match="uncommitted"):
        run_fix(
            load_finding("s3-no-encryption.json"),
            FixOptions(repo=work, github_repo="acme/infra"),
            runner_factory=runner_factory,
            github=FakeGitHub(),
        )
    assert "# local edit" in (work / "main.tf").read_text()  # the user's edit survives


def test_publish_requires_a_github_client(git_repo: tuple[Path, Path], runner_factory: Factory) -> None:
    work, _ = git_repo
    with pytest.raises(FixAborted, match="GitHub client"):
        run_fix(load_finding("s3-no-encryption.json"), FixOptions(repo=work), runner_factory=runner_factory)


def test_push_failure_before_commit_rolls_everything_back(git_repo: tuple[Path, Path], runner_factory: Factory) -> None:
    """If the branch already exists the run aborts and leaves the repo exactly as it found it."""
    work, _ = git_repo
    git(work, "branch", "fix/tf-s3-001-demo-logs")
    original = (work / "main.tf").read_text()
    with pytest.raises(FixAborted):
        run_fix(
            load_finding("s3-no-encryption.json"),
            FixOptions(repo=work, github_repo="acme/infra"),
            runner_factory=runner_factory,
            github=FakeGitHub(),
        )
    assert (work / "main.tf").read_text() == original
    assert git(work, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(work, "status", "--porcelain") == ""


def test_github_failure_after_push_keeps_the_branch_and_returns_to_the_users_branch(
    git_repo: tuple[Path, Path], runner_factory: Factory
) -> None:
    work, remote = git_repo

    class Boom(FakeGitHub):
        def open_pull_request(self, slug: str, **_kw: str) -> str:  # type: ignore[override]
            raise RuntimeError("GitHub is down")

    with pytest.raises(RuntimeError, match="GitHub is down"):
        run_fix(
            load_finding("s3-no-encryption.json"),
            FixOptions(repo=work, github_repo="acme/infra"),
            runner_factory=runner_factory,
            github=Boom(),
        )
    assert git(remote, "rev-parse", "fix/tf-s3-001-demo-logs")  # already pushed: not deleted behind the user's back
    assert git(work, "rev-parse", "--abbrev-ref", "HEAD") == "main"
