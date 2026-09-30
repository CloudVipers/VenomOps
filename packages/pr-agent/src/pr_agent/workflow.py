"""End-to-end flow: finding -> minimal Terraform fix -> validate/plan -> (dry-run | branch, commit, push, PR).

All edits are made first in a throw-away copy of the repository (the *sandbox*); the real repository is
only touched once everything has passed, and never when ``dry_run`` is set. The agent never merges.
"""

from __future__ import annotations

import difflib
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from findings_schema import Finding

from . import pr_body
from .fixes import AlreadyFixedError, Fixer, FixerError, FixOutcome, get_fixer
from .github_client import PullRequestClient, parse_remote
from .safety import CommandResult, SafeRunner, SecurityError, resolve_in_repo
from .tools import ToolBox, ToolError

RunnerFactory = Callable[[Path], SafeRunner]
Agent = Callable[[Finding, ToolBox], FixOutcome]

_IGNORED_DIRS = {".git", ".terraform", "__pycache__", "node_modules", ".venv"}
_IGNORED_SUFFIXES = (".tfstate", ".backup", ".tfvars", ".pem", ".key", ".tfplan")
_IGNORED_NAMES = {".env", "terraform.tfstate"}
# Written by `terraform init` (which the agent can trigger through terraform_validate before the diff is measured).
# They are not part of the fix and are never copied back to the real repository.
_TERRAFORM_ARTIFACTS = frozenset({".terraform.lock.hcl"})


class FixAborted(Exception):
    """The run stopped without changing anything; the message says why."""


class NothingToDo(FixAborted):
    """The code already contains the fix."""


@dataclass
class FixOptions:
    repo: Path
    dry_run: bool = False
    skip_plan: bool = False
    require_plan: bool = False
    github_repo: str | None = None
    base_branch: str | None = None
    max_changed_lines: int = 80


@dataclass
class FixReport:
    finding: Finding
    outcome: FixOutcome
    branch: str
    changed_files: dict[str, str]
    diff: str
    validate: CommandResult
    plan: CommandResult | None
    plan_note: str | None
    title: str
    body: str
    dry_run: bool
    pr_url: str | None = None


# ---- helpers ---------------------------------------------------------------------------------


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "fix"


def branch_name(finding: Finding) -> str:
    return f"fix/{slugify(finding.id)}-{slugify(finding.resource.name)}"


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in _IGNORED_DIRS or n in _IGNORED_NAMES or n.endswith(_IGNORED_SUFFIXES)}


def make_sandbox(repo: Path, dest: Path) -> Path:
    """Copy the repository (without VCS data, state, tfvars or keys) into ``dest``."""
    target = dest / "work"
    shutil.copytree(repo, target, ignore=_ignore, symlinks=True)
    return target


def snapshot(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            rel = p.relative_to(root)
            if not any(part in _IGNORED_DIRS for part in rel.parts) and rel.name not in _TERRAFORM_ARTIFACTS:
                files[rel.as_posix()] = p.read_bytes()
    return files


def _unified_diff(before: dict[str, bytes], after: dict[str, bytes], paths: list[str]) -> str:
    chunks: list[str] = []
    for path in paths:
        a = before[path].decode("utf-8").splitlines(keepends=True)
        b = after[path].decode("utf-8").splitlines(keepends=True)
        chunks.extend(difflib.unified_diff(a, b, f"a/{path}", f"b/{path}"))
    return "".join(chunks)


def verify_minimal(before: dict[str, bytes], after: dict[str, bytes], outcome: FixOutcome, max_lines: int) -> list[str]:
    """Abort unless only the files the fixer declared changed, and the diff is small.

    Returns the list of changed paths.
    """
    added, removed = set(after) - set(before), set(before) - set(after)
    if added or removed:
        raise FixAborted(f"unexpected files created/removed: {sorted(added | removed)}")
    changed = sorted(p for p in before if before[p] != after[p])
    if not changed:
        raise FixAborted("the fix produced no change")
    unexpected = [p for p in changed if p not in outcome.files or not p.endswith(".tf")]
    if unexpected:
        raise FixAborted(f"the fix touched files it did not declare: {unexpected}")
    diff = _unified_diff(before, after, changed)
    lines = sum(1 for ln in diff.splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))
    if lines > max_lines:
        raise FixAborted(f"the diff is too large to be a minimal fix ({lines} changed lines > {max_lines})")
    return changed


# ---- main flow ---------------------------------------------------------------------------------


def run_fix(
    finding: Finding,
    opts: FixOptions,
    *,
    runner_factory: RunnerFactory = SafeRunner,
    github: PullRequestClient | None = None,
    agent: Agent | None = None,
) -> FixReport:
    repo = opts.repo.resolve()
    if not repo.is_dir():
        raise FixAborted(f"repository directory not found: {repo}")

    fixer: Fixer | None = get_fixer(finding.id)
    if fixer is None and agent is None:
        raise FixAborted(
            f"there is no fixer for finding id {finding.id!r}; use --agent (optional, needs an explicit Bedrock model)"
        )

    with tempfile.TemporaryDirectory(prefix="pr-agent-") as tmp:
        sandbox = make_sandbox(repo, Path(tmp))
        tools = ToolBox(sandbox, runner_factory(sandbox))
        before = snapshot(sandbox)

        try:
            outcome = fixer.apply(finding, tools) if fixer is not None else _run_agent(agent, finding, tools)
        except AlreadyFixedError as exc:
            raise NothingToDo(str(exc)) from exc
        except (FixerError, ToolError, SecurityError) as exc:
            raise FixAborted(str(exc)) from exc

        after = snapshot(sandbox)
        changed = verify_minimal(before, after, outcome, opts.max_changed_lines)
        diff = _unified_diff(before, after, changed)

        validate = tools.terraform_validate()
        if not validate.ok:
            raise FixAborted(f"terraform validate failed after the fix:\n{validate.output}")

        plan: CommandResult | None = None
        plan_note: str | None = None
        if opts.skip_plan:
            plan_note = "omitido con --skip-plan"
        else:
            plan = tools.terraform_plan()
            if not plan.ok:
                if opts.require_plan:
                    raise FixAborted(f"terraform plan failed:\n{plan.output}")
                plan_note = "el plan falló en este entorno (p. ej. sin credenciales de AWS); ver el pipeline"

        report = FixReport(
            finding=finding,
            outcome=outcome,
            branch=branch_name(finding),
            changed_files={p: after[p].decode("utf-8") for p in changed},
            diff=diff,
            validate=validate,
            plan=plan,
            plan_note=plan_note,
            title=pr_body.title(finding, outcome),
            body="",
            dry_run=opts.dry_run,
        )
        report.body = pr_body.build(finding, outcome, diff, validate, plan, plan_note)

    if not opts.dry_run:
        _publish(repo, report, opts, runner_factory, github)
    return report


def _run_agent(agent: Agent | None, finding: Finding, tools: ToolBox) -> FixOutcome:
    assert agent is not None  # noqa: S101 - guarded by the caller
    return agent(finding, tools)


# ---- publishing (only when not dry-run) ----------------------------------------------------------


def _publish(
    repo: Path,
    report: FixReport,
    opts: FixOptions,
    runner_factory: RunnerFactory,
    github: PullRequestClient | None,
) -> None:
    if github is None:
        raise FixAborted("a GitHub client is required to open the pull request")
    git = runner_factory(repo)

    def run(*argv: str) -> CommandResult:
        res = git.run(["git", *argv])
        if not res.ok:
            raise FixAborted(f"git {' '.join(argv[:2])} failed: {res.output}")
        return res

    top = Path(run("rev-parse", "--show-toplevel").stdout.strip()).resolve()
    prefix = repo.relative_to(top)
    if run("status", "--porcelain").stdout.strip():
        raise FixAborted("the working tree has uncommitted changes; commit or stash them first")
    original = run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    slug = opts.github_repo or parse_remote(run("remote", "get-url", "origin").stdout)
    base = opts.base_branch or github.default_branch(slug)

    originals: dict[str, str] = {}
    committed = False
    run("switch", "-c", report.branch)
    try:
        for rel, content in report.changed_files.items():
            target = resolve_in_repo(repo, rel)
            originals[rel] = target.read_text(encoding="utf-8")
            target.write_text(content, encoding="utf-8")

        touched = {line.strip() for line in run("diff", "--name-only").stdout.splitlines() if line.strip()}
        expected = {(prefix / rel).as_posix() for rel in report.changed_files}
        if touched != expected:
            raise FixAborted(f"git sees different changes than planned: {sorted(touched ^ expected)}")

        run("add", "--", *report.changed_files)
        run("commit", "-m", _commit_message(report))
        committed = True
        run("push", "-u", "origin", report.branch)
        report.pr_url = github.open_pull_request(
            slug, title=report.title, body=report.body, head=report.branch, base=base
        )
    except BaseException:
        if not committed:  # nothing was committed: put the repository back exactly as it was
            for rel, text in originals.items():
                resolve_in_repo(repo, rel).write_text(text, encoding="utf-8")
            git.run(["git", "switch", original])
            git.run(["git", "branch", "-D", report.branch])
        raise
    finally:
        if committed:
            git.run(["git", "switch", original])  # leave the user's branch checked out


def _commit_message(report: FixReport) -> str:
    f = report.finding
    subject = f"fix(terraform): {report.outcome.summary.rstrip('.').replace('`', '')}"[:72]
    return (
        f"{subject}\n\n"
        f"Finding {f.id} ({f.severity.value}) on {f.resource.type}.{f.resource.name}.\n"
        "Generated by pr-agent; needs human review."
    )
