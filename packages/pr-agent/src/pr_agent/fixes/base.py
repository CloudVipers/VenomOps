"""Shared pieces for fixers: one fixer per type of finding, all working through the ToolBox."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Protocol

from findings_schema import Finding

from .. import hcl
from ..tools import ToolBox


class FixerError(Exception):
    """The finding cannot be fixed automatically (the workflow aborts without touching anything)."""


class AlreadyFixedError(FixerError):
    """The Terraform code already contains the fix: nothing to do."""


@dataclass(frozen=True)
class FixOutcome:
    """What a fixer did, used to build the PR."""

    summary: str
    details: list[str] = field(default_factory=list)
    risk: str = "low"  # low | medium | high
    # Every file the fixer is allowed to have changed: anything else in the diff aborts the run.
    files: frozenset[str] = frozenset()


class Fixer(Protocol):
    ids: ClassVar[frozenset[str]]
    title: ClassVar[str]

    def apply(self, finding: Finding, tools: ToolBox) -> FixOutcome: ...


def locate_resource(
    tools: ToolBox, finding: Finding, expected_types: frozenset[str] | None = None
) -> tuple[str, str, str]:
    """Find the resource block named by the finding. Returns ``(path, rtype, rname)``.

    Uses ``resource.path`` when present, otherwise searches every ``.tf`` file.
    """
    rtype, rname = finding.resource.type, finding.resource.name
    if expected_types is not None and rtype not in expected_types:
        raise FixerError(f"resource type {rtype!r} is not supported here (expected one of {sorted(expected_types)})")
    candidates = [finding.resource.path] if finding.resource.path else tools.list_files("**/*.tf")
    for path in candidates:
        try:
            text = tools.read_file(path)
        except Exception as exc:  # noqa: BLE001 - surface any tool problem as a fixer failure
            raise FixerError(f"cannot read {path}: {exc}") from exc
        if hcl.has_resource(text, rtype, rname):
            return path, rtype, rname
    raise FixerError(f"resource {rtype}.{rname} was not found in the Terraform files")
