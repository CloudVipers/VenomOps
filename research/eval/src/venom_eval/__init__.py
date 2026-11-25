"""Evaluation harness for the VenomOps research line (ADR 0011, docs/investigacion/metodologia.md)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
EXAMPLES = REPO_ROOT / "examples"
BENCHMARK = EXAMPLES / "benchmark"
