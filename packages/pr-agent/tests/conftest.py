from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from findings_schema import Finding

from pr_agent.safety import CommandResult, SafeRunner, check_command
from pr_agent.tools import ToolBox

ROOT = Path(__file__).resolve().parents[3]  # VenomOps monorepo root
EXAMPLES = ROOT / "examples"


class FakeTerraformRunner(SafeRunner):
    """Enforces the real allowlist but answers terraform commands with canned output (no provider download)."""

    def __init__(self, cwd: Path, calls: list[list[str]], *, validate_ok: bool = True, plan_ok: bool = True) -> None:
        super().__init__(cwd)
        self.calls = calls
        self.validate_ok = validate_ok
        self.plan_ok = plan_ok

    def run(self, argv: Sequence[str]) -> CommandResult:
        check_command(argv)  # the allowlist still applies to the fake
        if argv[0] != "terraform":
            return super().run(argv)
        self.calls.append(list(argv))
        sub = argv[1]
        if sub == "validate" and not self.validate_ok:
            return CommandResult(tuple(argv), 1, "", "Error: Invalid reference")
        if sub == "plan":
            if not self.plan_ok:
                return CommandResult(tuple(argv), 1, "", "Error: No valid credential sources found")
            return CommandResult(tuple(argv), 0, "Plan: 1 to add, 0 to change, 0 to destroy.\n", "")
        return CommandResult(tuple(argv), 0, "Success!\n", "")


@pytest.fixture
def tf_calls() -> list[list[str]]:
    return []


@pytest.fixture
def runner_factory(tf_calls: list[list[str]]) -> Callable[[Path], SafeRunner]:
    return lambda cwd: FakeTerraformRunner(cwd, tf_calls)


def copy_example(name: str, dest: Path) -> Path:
    target = dest / name
    shutil.copytree(EXAMPLES / "terraform" / name, target)
    return target


@pytest.fixture
def s3_repo(tmp_path: Path) -> Path:
    return copy_example("s3-demo", tmp_path)


def load_finding(name: str) -> Finding:
    return Finding.from_dict(json.loads((EXAMPLES / "findings" / name).read_text(encoding="utf-8")))


def make_finding(**overrides: Any) -> Finding:
    data: dict[str, Any] = json.loads((EXAMPLES / "findings" / "s3-no-encryption.json").read_text(encoding="utf-8"))
    data.update(overrides)
    return Finding.from_dict(data)


@pytest.fixture
def toolbox(s3_repo: Path) -> ToolBox:
    return ToolBox(s3_repo, SafeRunner(s3_repo))


def git(cwd: Path, *args: str) -> str:
    """Plain git for test setup/inspection only (not the agent's runner)."""
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)  # noqa: S603, S607
    return out.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo containing s3-demo with a local bare 'origin'. Returns (working repo, bare remote)."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)  # noqa: S603, S607
    work = copy_example("s3-demo", tmp_path)
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "test@example.com")
    git(work, "config", "user.name", "Test")
    (work / ".gitignore").write_text(".terraform/\n*.tfstate\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "initial")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "-u", "origin", "main")
    return work, remote
