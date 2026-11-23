"""Cross-check the committee against static analysis (Checkov, Trivy) to catch findings it may have dropped.

An injection that makes the agents ignore a resource leaves no trace in their output. Static analyzers do not read
natural language, so they are immune to that attack: every failed check on a resource the committee reported nothing
about (in security) is surfaced as an ``AC-STA-*`` finding and listed in the report. The analyzers are run by the
user; the committee only reads their JSON (it never runs them) and never sends it to a model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_INDEX = re.compile(r"\[[^\]]*\]$")


class StaticFindingsError(ValueError):
    """The file is not Checkov or Trivy JSON."""


@dataclass(frozen=True)
class StaticFinding:
    tool: str  # checkov | trivy
    rule: str
    resource: str  # Terraform address as the tool reports it
    title: str

    @property
    def base_address(self) -> str:
        return _INDEX.sub("", self.resource.strip())


def _from_checkov(data: Any) -> list[StaticFinding]:
    reports = data if isinstance(data, list) else [data]
    out = []
    for report in reports:
        for check in (report.get("results") or {}).get("failed_checks", []):
            out.append(
                StaticFinding(
                    "checkov",
                    str(check.get("check_id", "")),
                    str(check.get("resource", "")),
                    str(check.get("check_name", "")),
                )
            )
    return out


def _from_trivy(data: dict[str, Any]) -> list[StaticFinding]:
    out = []
    for result in data.get("Results") or []:
        for mis in result.get("Misconfigurations") or []:
            if mis.get("Status", "FAIL") != "FAIL":
                continue
            out.append(
                StaticFinding(
                    "trivy",
                    str(mis.get("ID", "")),
                    str((mis.get("CauseMetadata") or {}).get("Resource", "")),
                    str(mis.get("Title", "")),
                )
            )
    return out


def parse_static(data: Any) -> list[StaticFinding]:
    if isinstance(data, dict) and "Results" in data:
        return _from_trivy(data)
    if isinstance(data, list) or (isinstance(data, dict) and "results" in data):
        return _from_checkov(data)
    raise StaticFindingsError("not Checkov (`checkov -o json`) or Trivy (`trivy config -f json`) output")


def load_static(paths: list[Path]) -> list[StaticFinding]:
    out: list[StaticFinding] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StaticFindingsError(f"{path}: {exc}") from exc
        try:
            out.extend(parse_static(data))
        except StaticFindingsError as exc:
            raise StaticFindingsError(f"{path}: {exc}") from exc
    return [f for f in out if f.resource]


def uncovered(static: list[StaticFinding], covered_addresses: set[str]) -> list[StaticFinding]:
    """Static findings on resources the committee did not report in security (by base address, deduplicated)."""
    covered = {_INDEX.sub("", a) for a in covered_addresses}
    seen: set[tuple[str, str]] = set()
    out = []
    for f in sorted(static, key=lambda s: (s.base_address, s.tool, s.rule)):
        key = (f.base_address, f.rule)
        if f.base_address in covered or key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out
