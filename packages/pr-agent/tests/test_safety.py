"""The security boundaries are enforced in code: anything outside the allowlist raises."""

from __future__ import annotations

from pathlib import Path

import pytest

from pr_agent.safety import (
    CommandNotAllowedError,
    PathNotAllowedError,
    SafeRunner,
    check_command,
    resolve_in_repo,
)


@pytest.mark.parametrize(
    "argv",
    [
        ["terraform", "validate"],
        ["terraform", "validate", "-no-color"],
        ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
        ["terraform", "plan", "-input=false", "-no-color", "-lock=false", "-refresh=false"],
        ["git", "rev-parse", "--show-toplevel"],
        ["git", "status", "--porcelain"],
        ["git", "diff", "--no-color", "--stat"],
        ["git", "diff", "--no-color", "--", "s3.tf"],
        ["git", "remote", "get-url", "origin"],
        ["git", "switch", "-c", "fix/tf-s3-001-demo-logs"],
        ["git", "checkout", "-b", "fix/tf-s3-001-demo-logs"],
        ["git", "switch", "feature-x"],
        ["git", "add", "--", "modules/s3/main.tf", "main.tf"],
        ["git", "add", "--", "s3.tf"],
        ["git", "commit", "-m", "fix(s3): enable encryption"],
        ["git", "push", "-u", "origin", "fix/tf-s3-001-demo-logs"],
        ["git", "branch", "-D", "fix/tf-s3-001-demo-logs"],
    ],
)
def test_allowlisted_commands_pass(argv: list[str]) -> None:
    check_command(argv)


@pytest.mark.parametrize(
    "argv",
    [
        # Terraform: the destructive and state-changing verbs are never allowed.
        ["terraform", "apply"],
        ["terraform", "apply", "-auto-approve"],
        ["terraform", "destroy"],
        ["terraform", "import", "aws_s3_bucket.x", "b"],
        ["terraform", "state", "rm", "aws_s3_bucket.x"],
        ["terraform", "taint", "aws_s3_bucket.x"],
        ["terraform", "force-unlock", "abc"],
        ["terraform", "workspace", "delete", "prod"],
        ["terraform", "init"],  # init must never touch remote state
        ["terraform", "init", "-reconfigure"],
        ["terraform", "plan", "-destroy"],
        ["terraform", "plan", "-out=plan.tfplan"],
        ["terraform", "plan", "-target=aws_s3_bucket.x"],
        ["terraform", "validate", "-json"],
        ["terraform"],
        # kubectl / other programs
        ["kubectl", "delete", "pod", "x"],
        ["kubectl", "apply", "-f", "x.yaml"],
        ["aws", "s3", "rm", "s3://bucket", "--recursive"],
        ["bash", "-c", "terraform apply"],
        ["sh", "-c", "rm -rf /"],
        ["python", "-c", "print(1)"],
        ["/usr/bin/terraform", "validate"],  # full paths would bypass the exact-name check
        ["./terraform", "validate"],
        ["rm", "-rf", "."],
        [],
        # Git: force, deletes, protected branches, history rewriting, merges
        ["git", "push", "--force", "origin", "fix/x"],
        ["git", "push", "-f", "origin", "fix/x"],
        ["git", "push", "--force-with-lease", "origin", "fix/x"],
        ["git", "push", "origin", "main"],
        ["git", "push", "-u", "origin", "master"],
        ["git", "push", "origin", "+fix/x"],
        ["git", "push", "origin", ":fix/x"],
        ["git", "push", "origin", "--delete", "fix/x"],
        ["git", "push", "origin", "HEAD:main"],
        ["git", "push", "origin", "fix/x", "main"],
        ["git", "push"],
        ["git", "push", "-u", "origin", "feat/not-a-fix-branch"],
        ["git", "reset", "--hard", "HEAD~1"],
        ["git", "rebase", "-i", "HEAD~3"],
        ["git", "commit", "--amend", "-m", "x"],
        ["git", "commit", "-am", "x"],
        ["git", "merge", "fix/x"],
        ["git", "add", "-A"],
        ["git", "add", "."],
        ["git", "add", "--", "*"],
        ["git", "add", "--", "-A"],
        ["git", "clean", "-fdx"],
        ["git", "branch", "-D", "main"],
        ["git", "branch", "-D", "feature"],
        ["git", "checkout", "--", "."],
        ["git", "checkout", "main.tf"],  # would discard local changes to the file
        ["git", "checkout", "main"],  # use `git switch` to change branch
        ["git", "switch", "--discard-changes", "main"],
        ["git", "add", "--", "../other.tf"],
        ["git", "add", "--", "sub/../../other.tf"],
        ["git", "add", "--", "/etc/passwd"],
        ["git", "diff", "--", "../x.tf"],
        ["git", "checkout", "-f", "main"],
        ["git", "switch", "-c", "main"],
        ["git", "switch", "-c", "fix/../../etc"],
        ["git", "config", "user.email", "x@example.com"],
        ["git", "diff", "--output=/tmp/x"],
        ["git", "gc"],
    ],
)
def test_everything_else_is_rejected(argv: list[str]) -> None:
    with pytest.raises(CommandNotAllowedError):
        check_command(argv)


def test_runner_refuses_before_spawning_anything(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    runner = SafeRunner(tmp_path)
    with pytest.raises(CommandNotAllowedError):
        runner.run(["terraform", "apply", "-auto-approve"])
    with pytest.raises(CommandNotAllowedError):
        runner.run(["sh", "-c", f"touch {marker}"])
    assert not marker.exists()


def test_runner_runs_an_allowed_command(tmp_path: Path) -> None:
    result = SafeRunner(tmp_path).run(["git", "rev-parse", "--show-toplevel"])
    # tmp_path is not a repo: the command ran (and failed normally) instead of being refused.
    assert not result.ok
    assert result.argv == ("git", "rev-parse", "--show-toplevel")


def test_runner_does_not_leak_unrelated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pr_agent.safety import _sanitized_env

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_example")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-example")
    monkeypatch.setenv("AWS_PROFILE", "demo")
    env = _sanitized_env(None)
    assert "GITHUB_TOKEN" not in env and "OPENAI_API_KEY" not in env
    assert env["AWS_PROFILE"] == "demo" and env["TF_IN_AUTOMATION"] == "1"


# ---- paths -----------------------------------------------------------------------------


def test_paths_inside_the_repo_resolve(tmp_path: Path) -> None:
    (tmp_path / "modules").mkdir()
    f = tmp_path / "modules" / "s3.tf"
    f.write_text("")
    assert resolve_in_repo(tmp_path, "modules/s3.tf") == f.resolve()


@pytest.mark.parametrize(
    "bad",
    [
        "../secret.tf",
        "a/../../secret.tf",
        "/etc/passwd",
        "",
        ".git/config",
        ".terraform/providers/x",
        "terraform.tfstate",
        "prod.tfvars",
        "env/prod.auto.tfvars",
        "state/terraform.tfstate.backup",
        ".env",
        "keys/server.pem",
    ],
)
def test_dangerous_paths_are_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(PathNotAllowedError):
        resolve_in_repo(tmp_path, bad)


def test_symlink_escaping_the_repo_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.tf").write_text("")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "link").symlink_to(outside)
    with pytest.raises(PathNotAllowedError):
        resolve_in_repo(repo, "link/x.tf")


def test_a_missing_binary_is_reported_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On a clean machine without terraform the runner used to crash with FileNotFoundError and a traceback."""
    monkeypatch.setenv("PATH", str(tmp_path))  # an empty directory: nothing can be found
    result = SafeRunner(tmp_path).run(["terraform", "validate"])
    assert not result.ok and result.returncode == 127
    assert "was not found on PATH" in result.stderr and "terraform" in result.stderr
