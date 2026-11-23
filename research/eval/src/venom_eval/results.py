"""Run records (one JSON line per case and repetition) and the summary tables of the methodology.

Every record carries what is needed to reproduce and audit it: experiment, model id, condition, repetition, prompt
versions, temperature, git commit and UTC start time. Records are append-only JSON Lines under
``research/results/<run-id>/<experiment>.jsonl``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arch_committee.agents import prompt_versions as committee_prompt_versions
from pr_agent.agent import SYSTEM_PROMPT

from . import REPO_ROOT
from .metrics import mcnemar_exact, wilson

RESULTS = REPO_ROOT / "research" / "results"
TEMPERATURE = 0.0  # both pr-agent's agent and the committee call the model with temperature 0


def git_commit() -> str:
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, read-only
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def prompt_versions() -> dict[str, str]:
    versions = {"pr-agent": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]}
    versions.update({f"arch-committee.{k}": v for k, v in committee_prompt_versions().items()})
    return versions


def new_run_dir(experiment: str, base: Path = RESULTS) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = base / f"{stamp}-{experiment}"
    path.mkdir(parents=True, exist_ok=False)
    return path


class ResultsWriter:
    def __init__(self, out_dir: Path, experiment: str, meta: dict[str, Any]) -> None:
        self.path = out_dir / f"{experiment}.jsonl"
        self.meta = {
            "experiment": experiment,
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "git_commit": git_commit(),
            "prompt_versions": prompt_versions(),
            "temperature": TEMPERATURE,
            **meta,
        }
        out_dir.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps({**self.meta, **record}, ensure_ascii=False, default=str)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def load(directory: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(directory.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


# ---- summaries ----------------------------------------------------------------------------------------------------


def _rate(k: int, n: int) -> str:
    p, lo, hi = wilson(k, n)
    return f"{k}/{n} = {p:.1%} [{lo:.1%}, {hi:.1%}]"


def summarize_e1(records: list[dict[str, Any]]) -> str:
    rows: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: {"n": 0, "attempted": 0, "succeeded": 0})
    for r in records:
        key = (r["model_id"], r["condition"], r["goal"])
        rows[key]["n"] += 1
        rows[key]["attempted"] += int(r["verdict"]["attempted"])
        rows[key]["succeeded"] += int(r["verdict"]["succeeded"])
    lines = [
        "| Modelo | Condición | Objetivo | Intento | Éxito |",
        "|---|---|---|---|---|",
    ]
    for (model, cond, goal), row in sorted(rows.items()):
        lines.append(
            f"| {model} | {cond} | {goal} | {_rate(row['attempted'], row['n'])} | {_rate(row['succeeded'], row['n'])} |"
        )

    paired: dict[tuple[str, str, int], dict[str, bool]] = defaultdict(dict)
    for r in records:
        paired[(r["model_id"], r["case_id"], int(r.get("repetition", 0)))][r["condition"]] = r["verdict"]["succeeded"]
    b = sum(1 for v in paired.values() if v.get("prompt-only") and v.get("prompt+code") is False)
    c = sum(1 for v in paired.values() if v.get("prompt+code") and v.get("prompt-only") is False)
    if b or c:
        lines.append(f"\nMcNemar (éxito, solo prompt vs. prompt + código): b={b}, c={c}, p={mcnemar_exact(b, c):.4g}")
    return "\n".join(lines)


def summarize_e2(records: list[dict[str, Any]]) -> str:
    rows: dict[tuple[str, int, str], dict[str, int]] = defaultdict(
        lambda: {"n": 0, "applicable": 0, "succeeded": 0, "reported": 0}
    )
    for r in records:
        key = (r["model_id"], int(r["rounds"]), r["goal"])
        rows[key]["n"] += 1
        rows[key]["applicable"] += int(r["verdict"]["applicable"])
        rows[key]["succeeded"] += int(r["verdict"]["succeeded"])
        rows[key]["reported"] += int(r["verdict"]["injection_reported"])
    lines = ["| Modelo | Rondas | Objetivo | Éxito (sobre aplicables) | Inyección reportada |", "|---|---|---|---|---|"]
    for (model, rounds, goal), row in sorted(rows.items()):
        success, reported = _rate(row["succeeded"], row["applicable"]), _rate(row["reported"], row["n"])
        lines.append(f"| {model} | {rounds} | {goal} | {success} | {reported} |")
    return "\n".join(lines)


def summarize_e4(records: list[dict[str, Any]]) -> str:
    lines = ["| Redactor | Secretos filtrados | Texto inofensivo alterado |", "|---|---|---|"]
    for r in records:
        for name, res in sorted(r["redactors"].items()):
            leaked, altered = _rate(len(res["leaked"]), res["secrets"]), _rate(len(res["altered"]), res["benign"])
            lines.append(f"| {name} | {leaked} | {altered} |")
    return "\n".join(lines)


def summarize_e3(records: list[dict[str, Any]]) -> str:
    lines = ["| Sistema | Categoría | TP | FP | FN | Precision | Recall | F1 |", "|---|---|---|---|---|---|---|---|"]
    for r in records:
        for system, res in sorted(r["systems"].items()):
            for cat, m in res["by_category"].items():
                lines.append(
                    f"| {system} | {cat} | {m['tp']} | {m['fp']} | {m['fn']} | {m['precision']:.2f} | "
                    f"{m['recall']:.2f} | {m['f1']:.2f} |"
                )
    return "\n".join(lines)


SUMMARIES = {"e1": summarize_e1, "e2": summarize_e2, "e3": summarize_e3, "e4": summarize_e4}


def summarize(records: list[dict[str, Any]]) -> str:
    by_exp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_exp[r["experiment"]].append(r)
    parts = []
    for exp in sorted(by_exp):
        parts.append(f"## {exp.upper()}\n\n{SUMMARIES[exp](by_exp[exp])}")
    return "\n\n".join(parts) if parts else "(sin registros)"
