"""The labeled IaC benchmark (experiment E3) is consistent with its plans."""

from __future__ import annotations

import json
from typing import Any

import pytest
from arch_committee.plan_parser import parse_plan

from venom_eval import BENCHMARK
from venom_eval.e2 import base_address
from venom_eval.e3 import load_labels

IAC = BENCHMARK / "iac"
DOC: dict[str, Any] = json.loads((IAC / "labels.json").read_text(encoding="utf-8"))
CATEGORIES = {"security", "cost", "reliability", "operations"}
SEVERITIES = {"critical", "high", "medium", "low", "info"}


def test_size_and_balance() -> None:
    cases = DOC["cases"]
    clean = [c for c in cases if not c["expected"]]
    assert len(cases) >= 50 and len(clean) >= 10
    categories = {e["category"] for c in cases for e in c["expected"]}
    assert categories == CATEGORIES


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["id"] for c in DOC["cases"]])
def test_every_label_points_to_a_resource_of_its_plan(case: dict[str, Any]) -> None:
    plan_path = IAC / case["plan"]
    assert plan_path.exists(), f"missing plan: run `python build.py --plans {case['id']}`"
    plan = parse_plan(json.loads(plan_path.read_text(encoding="utf-8")))
    addresses = {base_address(r.address) for r in plan.resources}
    seen: set[tuple[str, str]] = set()
    for label in case["expected"]:
        assert label["category"] in CATEGORIES and label["severity"] in SEVERITIES
        for address in (label["resource"], *label.get("also", [])):
            assert base_address(address) in addresses, f"{case['id']}: {address} is not in the plan"
        key = (base_address(label["resource"]), label["category"])
        assert key not in seen, f"{case['id']}: two labels with the same resource and category"
        seen.add(key)


def test_labels_load_for_e3() -> None:
    labels = load_labels(IAC / "labels.json")
    assert len(labels) == len(DOC["cases"])
    assert sum(len(v) for v in labels.values()) == sum(len(c["expected"]) for c in DOC["cases"])


def test_plans_carry_no_credentials() -> None:
    for plan in IAC.glob("cases/*/plan.json"):
        text = plan.read_text(encoding="utf-8")
        assert "AKIA" not in text and "wJalrXUtnFEMI" not in text, plan
