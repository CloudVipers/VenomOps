from __future__ import annotations

import json
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[2]  # packages/findings-schema
EXAMPLES = PKG_ROOT / "examples"


def example_files(kind: str) -> list[Path]:
    return sorted((EXAMPLES / kind).glob("*.json"))


@pytest.fixture(scope="session")
def expected_errors() -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = json.loads(
        (EXAMPLES / "expected-errors.json").read_text(encoding="utf-8")
    )
    return data
