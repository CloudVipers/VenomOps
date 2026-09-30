"""``pr-agent`` command line."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from findings_schema import Finding, FindingValidationError

from . import __version__
from .agent import build_agent, make_bedrock_client
from .fixes import get_fixer
from .github_client import PullRequestClient, PyGithubClient
from .safety import SafeRunner, SecurityError
from .workflow import Agent, FixAborted, FixOptions, FixReport, NothingToDo, RunnerFactory, run_fix

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Turns a VenomOps finding into a Pull Request with the minimal Terraform fix. Never merges.",
)

MODEL_ENV = "PR_AGENT_BEDROCK_MODEL"

# Indirection points so tests never spawn terraform or call GitHub.
RUNNER_FACTORY: RunnerFactory = SafeRunner


def _github() -> PullRequestClient:
    return PyGithubClient()


GITHUB_FACTORY = _github


def _usage_error(message: str) -> typer.Exit:
    """Plain-text usage error (exit 2). Avoids rich's boxed output, which wraps and colours long messages."""
    typer.secho(f"Error: {message}", fg="red", err=True)
    return typer.Exit(2)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"pr-agent {__version__}")
        raise typer.Exit()


def _resource_of(d: Any) -> dict[str, Any]:
    res = d.get("resource") if isinstance(d, dict) else None
    return res if isinstance(res, dict) else {}


def _where(d: Any) -> str:
    res = _resource_of(d)
    return "/".join(str(x) for x in (res.get("namespace"), res.get("name")) if x)


def _describe(i: int, d: Any) -> str:
    return f"[{i}] {d.get('id', '?') if isinstance(d, dict) else '?'} {_where(d)}".rstrip()


def _select(data: list[Any], index: int | None, finding_id: str | None, resource: str | None, supported: bool) -> Any:
    """Pick ONE finding from an array (e.g. ``kdoctor -o json``): by position, or by filters matching exactly one."""
    if index is not None:
        if not 0 <= index < len(data):
            raise _usage_error(f"--index {index} is out of range (0..{len(data) - 1})")
        return data[index]

    candidates = [(i, d) for i, d in enumerate(data) if isinstance(d, dict)]
    if finding_id:
        candidates = [(i, d) for i, d in candidates if d.get("id") == finding_id]
    if resource:
        candidates = [(i, d) for i, d in candidates if resource in (_resource_of(d).get("name"), _where(d))]
    if supported:
        candidates = [(i, d) for i, d in candidates if isinstance(d.get("id"), str) and get_fixer(d["id"]) is not None]

    if len(candidates) == 1:
        return candidates[0][1]
    listing = ", ".join(_describe(i, d) for i, d in (candidates or list(enumerate(data))))
    if not candidates:
        raise _usage_error(f"no finding matches the filters; the input has: {listing}")
    raise _usage_error(f"{len(candidates)} findings match; narrow it with --id/--resource/--index ({listing})")


def _load_finding(
    source: str, index: int | None, finding_id: str | None, resource: str | None, supported: bool
) -> Finding:
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _usage_error(f"{source}: not valid JSON ({exc.msg}, line {exc.lineno})") from exc

    if isinstance(data, list):  # e.g. the output of `kdoctor -o json`
        if not data:
            raise _usage_error("the input is an empty array: there is no finding to fix")
        if len(data) == 1 and index is None and not (finding_id or resource or supported):
            data = data[0]
        else:
            data = _select(data, index, finding_id, resource, supported)
    try:
        return Finding.from_dict(data)
    except FindingValidationError as exc:
        typer.secho(f"The finding does not satisfy findings-schema:\n{exc}", fg="red", err=True)
        raise typer.Exit(2) from exc


def _print_report(report: FixReport) -> None:
    typer.secho(f"Finding {report.finding.id}: {report.outcome.summary}", bold=True)
    typer.echo(f"Branch:  {report.branch}")
    typer.echo(f"Files:   {', '.join(report.changed_files)}")
    typer.echo(f"Risk:    {report.outcome.risk}")
    if report.dry_run:
        typer.secho("\n--- diff (dry-run) ---", bold=True)
        typer.echo(report.diff.rstrip())
        typer.secho(f"\n--- PR title ---\n{report.title}", bold=True)
        typer.secho("\n--- PR body ---", bold=True)
        typer.echo(report.body.rstrip())
        typer.secho("\nDry-run: nothing was written, committed or pushed.", fg="yellow")
    else:
        typer.secho(f"\nPull request opened (needs human review; it is never merged): {report.pr_url}", fg="green")


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version.")
    ] = False,
) -> None:
    """pr-agent."""


@app.command()
def fix(
    finding: Annotated[
        str, typer.Option("--finding", help="Finding JSON file (an array is accepted), or '-' for stdin.")
    ],
    repo: Annotated[Path, typer.Option("--repo", help="Terraform directory (inside a git repo to open a PR).")] = Path(
        "."
    ),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the diff and the PR body without touching GitHub or the repo.")
    ] = False,
    index: Annotated[
        int | None, typer.Option("--index", help="Position of the finding when the input is an array.")
    ] = None,
    finding_id: Annotated[str | None, typer.Option("--id", help="Pick the finding with this id from an array.")] = None,
    resource: Annotated[str | None, typer.Option("--resource", help="Pick by resource name or namespace/name.")] = None,
    supported: Annotated[
        bool, typer.Option("--supported", help="From an array, pick the one finding with a fixer.")
    ] = False,
    skip_plan: Annotated[bool, typer.Option("--skip-plan", help="Do not run terraform plan.")] = False,
    require_plan: Annotated[bool, typer.Option("--require-plan", help="Abort if terraform plan fails.")] = False,
    github_repo: Annotated[
        str | None, typer.Option("--github-repo", help="owner/name (default: from the 'origin' remote).")
    ] = None,
    base: Annotated[
        str | None, typer.Option("--base", help="Base branch for the PR (default: the repo's default branch).")
    ] = None,
    agent: Annotated[
        bool, typer.Option("--agent", help="Use the optional LLM agent when there is no deterministic fixer.")
    ] = False,
    model_id: Annotated[
        str | None, typer.Option("--model-id", help=f"Bedrock model for --agent (or ${MODEL_ENV}); no default.")
    ] = None,
    region: Annotated[str | None, typer.Option("--region", help="AWS region for Bedrock (--agent).")] = None,
) -> None:
    """Fix FINDING with the minimal Terraform change and open a PR (or print it with --dry-run)."""
    parsed = _load_finding(finding, index, finding_id, resource, supported)

    llm_agent: Agent | None = None
    if agent:
        model = model_id or os.environ.get(MODEL_ENV, "")
        if not model.strip():
            raise _usage_error(f"--agent needs an explicit model (--model-id or ${MODEL_ENV}); there is no default")
        try:
            llm_agent = build_agent(make_bedrock_client(region), model)
        except RuntimeError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from exc

    github: PullRequestClient | None = None
    if not dry_run:
        try:
            github = GITHUB_FACTORY()
        except RuntimeError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from exc

    opts = FixOptions(
        repo=repo,
        dry_run=dry_run,
        skip_plan=skip_plan,
        require_plan=require_plan,
        github_repo=github_repo,
        base_branch=base,
    )
    try:
        report = run_fix(parsed, opts, runner_factory=RUNNER_FACTORY, github=github, agent=llm_agent)
    except NothingToDo as exc:
        typer.secho(f"Nothing to do: {exc}", fg="yellow")
        raise typer.Exit(0) from exc
    except (FixAborted, SecurityError) as exc:
        typer.secho(f"Aborted: {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    _print_report(report)


if __name__ == "__main__":
    app()
