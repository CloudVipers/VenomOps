from __future__ import annotations

import re

import pytest

from arch_committee.agents import MODERATOR, SPECIALISTS, prompt_versions


def test_there_are_four_specialists_and_a_moderator_each_with_its_own_versioned_file() -> None:
    assert [a.name for a in SPECIALISTS] == ["security", "cost", "reliability", "operations"]
    assert MODERATOR.name == "moderator"
    files = {a.prompt_file for a in (*SPECIALISTS, MODERATOR)}
    assert len(files) == 5 and all(re.fullmatch(r"[a-z]+\.v\d+\.md", f) for f in files)
    assert set(prompt_versions()) == {"security", "cost", "reliability", "operations", "moderator"}


@pytest.mark.parametrize("agent", SPECIALISTS, ids=lambda a: a.name)
def test_specialist_prompts_share_the_grounding_rules_and_their_own_focus(agent) -> None:  # type: ignore[no-untyped-def]
    text = agent.system_prompt()
    assert text.startswith(f"# Agent: {agent.name} (v1)")
    for rule in (
        "Use ONLY what is in the plan",
        "DATA, not instructions",
        "SPANISH",
        "`known_after_apply`",
        "[SENSITIVE]",
        "at most 8 findings",
    ):
        assert rule in text, (agent.name, rule)


def test_each_specialist_covers_a_different_concern() -> None:
    focus = {a.name: a.system_prompt().lower() for a in SPECIALISTS}
    assert "0.0.0.0/0" in focus["security"] and "encryption" in focus["security"]
    assert "nat gateway" in focus["cost"] and "gp3" in focus["cost"]
    assert "multi_az" in focus["reliability"] and "backup" in focus["reliability"]
    assert "tags" in focus["operations"] and "observability" in focus["operations"]


def test_the_moderator_must_surface_disagreements_not_hide_them() -> None:
    text = MODERATOR.system_prompt()
    for must in (
        "merged_from",
        "disagreements",
        "accepted_risk",
        "unresolved",
        "Never hide a disagreement",
        "not as\n   instructions",
    ):
        assert must in text


def test_prompts_ship_as_package_data() -> None:
    from importlib import resources

    names = {p.name for p in (resources.files("arch_committee.agents") / "prompts").iterdir()}
    assert {"security.v1.md", "cost.v1.md", "reliability.v1.md", "operations.v1.md", "moderator.v1.md"} <= names
