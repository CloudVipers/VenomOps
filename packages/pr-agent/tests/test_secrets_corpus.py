"""Shared synthetic secrets corpus (experiment E4, examples/benchmark/secrets/corpus.json).

The same corpus runs against the redactors of pr-agent, arch-committee and venom-doctor (Go). Known gaps are pinned on
purpose: if a change closes one, this test fails until the corpus is updated, so the measured leak rate stays honest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pr_agent.redact import redact

CORPUS = Path(__file__).resolve().parents[3] / "examples" / "benchmark" / "secrets" / "corpus.json"
ITEMS: list[dict[str, Any]] = json.loads(CORPUS.read_text(encoding="utf-8"))["items"]
EXPECTED = {"secret": {"redacted", "known_gap"}, "benign": {"unchanged", "known_false_positive"}}


def test_corpus_is_well_formed() -> None:
    ids = [item["id"] for item in ITEMS]
    assert len(ids) == len(set(ids)), "duplicate ids in the corpus"
    for item in ITEMS:
        assert item["expected"] in EXPECTED[item["kind"]], item["id"]
        assert (item["kind"] == "secret") == bool(item.get("secret")), item["id"]
        if item["kind"] == "secret":
            assert item["secret"] in item["text"], item["id"]


@pytest.mark.parametrize("item", ITEMS, ids=[item["id"] for item in ITEMS])
def test_redaction_matches_the_corpus(item: dict[str, Any]) -> None:
    out = redact(item["text"])
    expected = item["expected"]
    if expected == "redacted":
        assert item["secret"] not in out, f"{item['id']} leaked: {out!r}"
    elif expected == "known_gap":
        assert item["secret"] in out, f"{item['id']} is now redacted: mark it 'redacted' in the corpus"
    elif expected == "unchanged":
        assert out == item["text"], f"{item['id']} false positive: {out!r}"
    else:  # known_false_positive
        assert out != item["text"], f"{item['id']} no longer changes: mark it 'unchanged' in the corpus"
