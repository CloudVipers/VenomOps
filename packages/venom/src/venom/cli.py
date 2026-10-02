"""`venom`: one entry point for the three VenomOps tools.

``review`` and ``fix`` are the very same commands as ``arch-committee review`` and ``pr-agent fix`` (registered, not
copied), so options, safety rules and tests are not duplicated. ``doctor`` hands the arguments untouched to the
``kubectl-venom_doctor`` binary (Go), which stays the krew plugin ``kubectl venom-doctor``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Annotated

import typer
from arch_committee.cli import review as _review
from pr_agent.cli import fix as _fix

from . import __version__
from .update import update_command

DOCTOR_BINARY = "kubectl-venom_doctor"

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="VenomOps: diagnose a cluster (doctor), fix a finding with a PR (fix) and review a Terraform plan (review).",
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"venom {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version.")
    ] = False,
) -> None:
    """venom."""


app.command("review", help="A virtual committee reviews a Terraform plan (same as `arch-committee review`).")(_review)
app.command("fix", help="Turn a finding into a PR with the minimal Terraform fix (same as `pr-agent fix`).")(_fix)


app.command(
    "update",
    help="Check for the latest venom and download its package for this system, verified (--install installs it).",
)(update_command)


@app.command(
    "doctor",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True, "help_option_names": []},
    help="Explain why something is broken in a cluster (runs `kubectl-venom_doctor`; pass its flags, e.g. -n NS).",
)
def doctor(ctx: typer.Context) -> None:
    binary = shutil.which(DOCTOR_BINARY)
    if binary is None:
        typer.secho(
            f"Error: `{DOCTOR_BINARY}` is not on PATH "
            f"(install venom-doctor, e.g. `kubectl krew install venom-doctor`).",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)
    # Fixed executable, no shell: the user's flags are passed as plain arguments.
    result = subprocess.run([binary, *ctx.args], check=False)  # noqa: S603
    sys.stdout.flush()
    raise typer.Exit(result.returncode)
