"""The committee members: four specialists and a moderator. Each has a versioned system prompt in ``prompts/``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib import resources


@dataclass(frozen=True)
class AgentSpec:
    name: str  # security | cost | reliability | operations | moderator
    code: str  # id prefix for the findings it raises (SEC-1, COST-2, ...)
    prompt_file: str  # e.g. security.v1.md

    @property
    def version(self) -> str:
        m = re.search(r"\.(v\d+)\.md$", self.prompt_file)
        return m.group(1) if m else "v?"

    @cache  # noqa: B019 - AgentSpec is frozen/hashable; the prompt is immutable package data
    def system_prompt(self) -> str:
        return (resources.files(__package__) / "prompts" / self.prompt_file).read_text(encoding="utf-8")


SPECIALISTS: tuple[AgentSpec, ...] = (
    AgentSpec("security", "SEC", "security.v1.md"),
    AgentSpec("cost", "COST", "cost.v1.md"),
    AgentSpec("reliability", "REL", "reliability.v1.md"),
    AgentSpec("operations", "OPS", "operations.v1.md"),
)
MODERATOR = AgentSpec("moderator", "MOD", "moderator.v1.md")


def prompt_versions() -> dict[str, str]:
    """Agent name -> prompt version, recorded in every report for traceability."""
    return {a.name: a.version for a in (*SPECIALISTS, MODERATOR)}
