"""``arch-committee`` command line."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .agents import MODERATOR, SPECIALISTS
from .context import ContextError, load_context_findings, render_context
from .llm import LLM, make_bedrock_llm
from .orchestrator import CommitteeConfig, run_committee
from .plan_parser import PlanError, PlanModel, load_plan, render_for_llm
from .report import write_outputs

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="A virtual architecture committee reviews a Terraform plan, debates it, and reports. Applies nothing.",
)

MODEL_ENV = "ARCH_COMMITTEE_BEDROCK_MODEL"

# Indirection so tests never call Bedrock.
LLM_FACTORY = make_bedrock_llm


def _usage_error(message: str) -> typer.Exit:
    """Plain-text usage error (exit 2); avoids rich's boxed output, which wraps long messages."""
    typer.secho(f"Error: {message}", fg="red", err=True)
    return typer.Exit(2)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"arch-committee {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version.")
    ] = False,
) -> None:
    """arch-committee."""


def _describe_plan(plan: PlanModel) -> None:
    acts = ", ".join(f"{n} {a}" for a, n in plan.action_counts.items())
    typer.echo(f"Plan: Terraform {plan.terraform_version}, {len(plan.resources)} resources ({acts})")
    typer.echo("Types: " + ", ".join(f"{t} x{n}" for t, n in plan.type_counts.items()))


@app.command()
def review(
    plan: Annotated[Path, typer.Option("--plan", help="Output of `terraform show -json plan.out`.")],
    out: Annotated[Path, typer.Option("--out", help="Directory for report.md and findings.json.")] = Path("./out"),
    model_id: Annotated[
        str | None, typer.Option("--model-id", help=f"Bedrock model (or ${MODEL_ENV}); no default.")
    ] = None,
    region: Annotated[str | None, typer.Option("--region", help="AWS region for Bedrock.")] = None,
    max_tokens: Annotated[
        int, typer.Option("--max-tokens", min=1000, help="Total token budget for the whole run.")
    ] = 200_000,
    max_rounds: Annotated[
        int, typer.Option("--max-rounds", min=1, max=2, help="1 = analysis only, 2 = adds the rebuttal round.")
    ] = 2,
    max_output_tokens: Annotated[
        int, typer.Option("--max-output-tokens", min=1024, max=64000, help="Output cap of each model call.")
    ] = 8192,
    context_findings: Annotated[
        list[Path] | None,
        typer.Option(
            "--context-findings", help="findings JSON (one or an array, e.g. venom-doctor -o json) as prior context."
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be sent (already redacted); call no model.")
    ] = False,
) -> None:
    """Review PLAN with the committee and write report.md and findings.json into OUT."""
    try:
        parsed = load_plan(plan)
    except (PlanError, OSError) as exc:
        raise _usage_error(str(exc)) from exc

    try:
        context = load_context_findings(context_findings or [])
    except ContextError as exc:
        raise _usage_error(str(exc)) from exc

    config = CommitteeConfig(max_total_tokens=max_tokens, max_rounds=max_rounds, max_output_tokens=max_output_tokens)
    _describe_plan(parsed)
    if context:
        typer.echo(f"Context: {len(context)} prior findings ({', '.join(sorted({f.source.value for f in context}))})")

    if dry_run:
        payload, truncated = render_for_llm(parsed, config.plan_max_chars)
        per_call = [(a.name, (len(a.system_prompt()) + len(payload)) // 4) for a in SPECIALISTS]
        typer.secho("\n--- Dry run: no model is called and nothing is written ---", bold=True)
        typer.echo(f"Agents: {', '.join(a.name for a in SPECIALISTS)} + {MODERATOR.name}; rounds: {max_rounds}")
        for name, tokens in per_call:
            typer.echo(f"  round 1 · {name}: ~{tokens:,} input tokens")
        typer.echo(f"Budget: {max_tokens:,} tokens" + (" (plan values shortened to fit)" if truncated else ""))
        typer.secho("\n--- Redacted plan payload that agents would receive ---", bold=True)
        typer.echo(payload)
        if context:
            typer.secho("\n--- Redacted prior findings that agents would receive ---", bold=True)
            typer.echo(render_context(context)[0])
        return

    model = (model_id or os.environ.get(MODEL_ENV, "")).strip()
    if not model:
        raise _usage_error(f"a model is required (--model-id or ${MODEL_ENV}); there is no default")
    try:
        llm: LLM = LLM_FACTORY(model, region)
    except RuntimeError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc

    result = run_committee(parsed, llm, config, context)
    report_path, findings_path, warnings = write_outputs(result, out, model)

    typer.secho("\nCommittee finished.", bold=True)
    typer.echo(f"Findings: {len(result.final) - len(warnings)}  Disagreements: {len(result.disagreements)}")
    typer.echo(f"Tokens: {result.tokens_used:,} / {result.tokens_max:,}  Rounds: {result.rounds_run}")
    typer.echo(f"Report:   {report_path}")
    typer.echo(f"Findings: {findings_path}")
    for note in [*result.notes, *warnings]:
        typer.secho(f"note: {note}", fg="yellow")


if __name__ == "__main__":
    app()
