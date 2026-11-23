"""Experiment E3: detection compared (hypothesis H3).

Normalizes the output of each system to predictions ``(resource, category)`` and matches them against the labels of
the IaC benchmark (examples/benchmark/iac/labels.json). Static tools (Checkov, Trivy) run outside this harness; only
their JSON output is read (ADR 0011, decision 5).

Matching rule, fixed before running the experiments: a prediction is a true positive when its base resource address
(without ``[index]``) is the label's resource or one of its declared aliases (``also``, e.g. the bucket for a public
bucket ACL) and its category equals the label's, and the label is still unmatched. Duplicate predictions collapse.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .e2 import CATEGORY_BY_CODE, base_address
from .metrics import prf


@dataclass(frozen=True)
class Prediction:
    resource: str
    category: str
    rule: str = ""


@dataclass(frozen=True)
class Label:
    resource: str
    category: str
    severity: str = ""
    also: tuple[str, ...] = ()

    def addresses(self) -> frozenset[str]:
        return frozenset(base_address(a) for a in (self.resource, *self.also))


def load_labels(path: Path) -> dict[str, list[Label]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        case["id"]: [
            Label(e["resource"], e["category"], e.get("severity", ""), tuple(e.get("also", ())))
            for e in case["expected"]
        ]
        for case in data["cases"]
    }


# ---- adapters ----------------------------------------------------------------------------------------------------


def from_checkov(data: Any) -> list[Prediction]:
    """``checkov -o json``: one report or a list of reports (one per framework)."""
    reports = data if isinstance(data, list) else [data]
    out = []
    for report in reports:
        for check in (report.get("results") or {}).get("failed_checks", []):
            out.append(Prediction(str(check.get("resource", "")), "security", str(check.get("check_id", ""))))
    return out


def from_trivy(data: Any) -> list[Prediction]:
    """``trivy config -f json``: failed misconfigurations with their cause resource."""
    out = []
    for result in data.get("Results") or []:
        for mis in result.get("Misconfigurations") or []:
            if mis.get("Status", "FAIL") != "FAIL":
                continue
            resource = (mis.get("CauseMetadata") or {}).get("Resource", "")
            out.append(Prediction(str(resource), "security", str(mis.get("ID", ""))))
    return out


def from_findings(data: Any) -> list[Prediction]:
    """VenomOps findings (findings.json of arch-committee): category from the id, e.g. AC-SEC-001 -> security."""
    items = data if isinstance(data, list) else [data]
    out = []
    for f in items:
        code = str(f.get("id", "")).split("-")[1] if str(f.get("id", "")).count("-") >= 2 else ""
        res = f.get("resource") or {}
        out.append(Prediction(f"{res.get('type')}.{res.get('name')}", CATEGORY_BY_CODE.get(code, "unknown"), f["id"]))
    return out


ADAPTERS = {"checkov": from_checkov, "trivy": from_trivy, "findings": from_findings}


# ---- matching ----------------------------------------------------------------------------------------------------


def match(predictions: list[Prediction], labels: list[Label]) -> dict[str, dict[str, int]]:
    """Counts per category (and ``all``): tp, fp, fn."""
    preds = {(base_address(p.resource), p.category) for p in predictions if p.resource}
    remaining = list(labels)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for address, category in sorted(preds):
        hit = next((lb for lb in remaining if lb.category == category and address in lb.addresses()), None)
        if hit is not None:
            remaining.remove(hit)
            counts[category]["tp"] += 1
        elif any(lb.category == category and address in lb.addresses() for lb in labels):
            continue  # a second alias of an already matched label is the same finding, not a false positive
        else:
            counts[category]["fp"] += 1
    for lb in remaining:
        counts[lb.category]["fn"] += 1
    total = {k: sum(c[k] for c in counts.values()) for k in ("tp", "fp", "fn")}
    return {**counts, "all": total}


def score(per_case: dict[str, list[Prediction]], labels: dict[str, list[Label]]) -> dict[str, Any]:
    """Aggregate over all labeled cases (a case without predictions counts all its labels as misses)."""
    agg: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    per_case_f1: dict[str, float] = {}
    for case_id, case_labels in labels.items():
        counts = match(per_case.get(case_id, []), case_labels)
        for category, c in counts.items():
            for k in ("tp", "fp", "fn"):
                agg[category][k] += c[k]
        per_case_f1[case_id] = prf(**counts["all"])["f1"]
    return {
        "by_category": {cat: {**c, **prf(**c)} for cat, c in sorted(agg.items())},
        "per_case_f1": per_case_f1,
    }


def union(*systems: dict[str, list[Prediction]]) -> dict[str, list[Prediction]]:
    out: dict[str, list[Prediction]] = defaultdict(list)
    for system in systems:
        for case_id, preds in system.items():
            out[case_id].extend(preds)
    return dict(out)


def load_system(directory: Path, adapter: str) -> dict[str, list[Prediction]]:
    """One JSON output per case: ``<directory>/<case_id>.json``."""
    parse = ADAPTERS[adapter]
    return {p.stem: parse(json.loads(p.read_text(encoding="utf-8"))) for p in sorted(directory.glob("*.json"))}
