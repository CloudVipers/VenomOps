"""Experiment E4: confidentiality (hypothesis H4). Deterministic: runs the secrets corpus through every redactor."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from arch_committee.redact import redact as committee_redact
from pr_agent.redact import redact as pr_agent_redact

from . import BENCHMARK
from .metrics import wilson

CORPUS = BENCHMARK / "secrets" / "corpus.json"
REDACTORS: dict[str, Callable[[str], str]] = {"pr-agent": pr_agent_redact, "arch-committee": committee_redact}


def evaluate(redactor: Callable[[str], str], corpus: Path = CORPUS) -> dict[str, Any]:
    items = json.loads(corpus.read_text(encoding="utf-8"))["items"]
    secrets = [i for i in items if i["kind"] == "secret"]
    benign = [i for i in items if i["kind"] == "benign"]
    leaked = [i["id"] for i in secrets if i["secret"] in redactor(i["text"])]
    altered = [i["id"] for i in benign if redactor(i["text"]) != i["text"]]
    leak = wilson(len(leaked), len(secrets))
    fp = wilson(len(altered), len(benign))
    by_category: dict[str, dict[str, int]] = {}
    for i in secrets:
        c = by_category.setdefault(i["category"], {"n": 0, "leaked": 0})
        c["n"] += 1
        c["leaked"] += int(i["id"] in leaked)
    return {
        "secrets": len(secrets),
        "leaked": leaked,
        "leak_rate": {"value": leak[0], "ci95": [leak[1], leak[2]]},
        "benign": len(benign),
        "altered": altered,
        "false_positive_rate": {"value": fp[0], "ci95": [fp[1], fp[2]]},
        "by_category": by_category,
    }


def run_all(corpus: Path = CORPUS) -> dict[str, dict[str, Any]]:
    return {name: evaluate(fn, corpus) for name, fn in REDACTORS.items()}
