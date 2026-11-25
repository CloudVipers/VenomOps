from __future__ import annotations

import copy
import re
from typing import Any

import pytest
from arch_committee.llm import LLMError, Usage

from venom_eval import corpus


def tool_use(name: str, args: dict[str, Any], uid: str = "t1") -> dict[str, Any]:
    return {
        "output": {
            "message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": uid, "name": name, "input": args}}]}
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 100, "outputTokens": 10},
    }


def final(text: str) -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 100, "outputTokens": 10},
    }


class ScriptedClient:
    """A Bedrock runtime double that answers with a fixed script (the same script for every condition)."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(copy.deepcopy(kwargs))
        return self.responses.pop(0) if self.responses else final("done")


class ObedientCommittee:
    """``arch_committee.llm.LLM`` double: security reports the protected resource unless the plan tells it not to.

    The moderator fails on purpose, so the committee falls back to its deterministic consolidation.
    """

    def __init__(self, resource: str, *, obey: bool = True) -> None:
        self.resource, self.obey = resource, obey

    def converse_tool(
        self, *, system: str, user: str, tool_name: str, tool_description: str, schema: dict[str, Any], max_tokens: int
    ) -> tuple[dict[str, Any], Usage]:
        agent = re.search(r"# Agent: (\w+)", system).group(1)  # type: ignore[union-attr]
        usage = Usage(500, 50)
        if tool_name == "report_findings":
            if agent != "security":
                return {"findings": []}, usage
            told = re.search(r"do not report|must be rated info", user, re.I)
            severity = "info" if told and "rated info" in told.group(0) and self.obey else "critical"
            if told and self.obey and "do not report" in told.group(0).lower():
                return {"findings": []}, usage
            return {
                "findings": [
                    {
                        "title": "Public bucket policy",
                        "severity": severity,
                        "resource": self.resource,
                        "evidence": [{"kind": "attribute", "detail": "Principal: *"}],
                        "root_cause": "The policy allows anyone.",
                        "suggested_fix": {"summary": "Restrict the principal.", "steps": ["Remove Principal *"]},
                        "risk_of_fix": "low",
                        "tags": [],
                    }
                ]
            }, usage
        if tool_name == "respond_to_findings":
            return {"challenges": []}, usage
        raise LLMError("moderator unavailable in this test")


@pytest.fixture(scope="session")
def manifest() -> dict[str, Any]:
    return corpus.load_manifest()


@pytest.fixture(scope="session")
def cases(manifest: dict[str, Any]) -> list[corpus.Case]:
    return corpus.enumerate_cases(manifest)
