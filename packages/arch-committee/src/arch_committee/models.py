"""Structured data exchanged with the agents (also the JSON Schemas forced on the model via tool use)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["critical", "high", "medium", "low", "info"]
Risk = Literal["low", "medium", "high"]
Stance = Literal["agree", "disagree", "refine"]
Resolution = Literal["resolved", "accepted_risk", "unresolved"]


class _Lenient(BaseModel):
    """Models ignore extra keys: a chatty model must not break the run (the orchestrator validates content)."""

    model_config = ConfigDict(extra="ignore")


class Evidence(_Lenient):
    kind: str = Field(min_length=1, description="Short label, e.g. 'attribute', 'plan', 'topology'.")
    detail: str = Field(min_length=1, description="The observable proof taken from the plan.")


class Fix(_Lenient):
    summary: str = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    iac_hint: str | None = Field(default=None, description="Terraform snippet or attribute change, if helpful.")


class AgentFinding(_Lenient):
    title: str = Field(min_length=1, max_length=200)
    severity: Severity
    resource: str = Field(
        description="Terraform address from the plan (e.g. aws_db_instance.orders) or 'plan' for plan-wide issues."
    )
    evidence: list[Evidence] = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    suggested_fix: Fix
    risk_of_fix: Risk
    tags: list[str] = Field(default_factory=list)


class Round1Output(_Lenient):
    findings: list[AgentFinding] = Field(default_factory=list, max_length=12)


class Challenge(_Lenient):
    target: str = Field(description="Id of another agent's finding, e.g. SEC-1.")
    stance: Stance
    argument: str = Field(min_length=1, description="Grounded in the plan or in an explicit trade-off.")
    suggested_severity: Severity | None = None


class Round2Output(_Lenient):
    challenges: list[Challenge] = Field(default_factory=list, max_length=20)


class Position(_Lenient):
    agent: str
    stance: str = Field(min_length=1)


class Disagreement(_Lenient):
    topic: str = Field(min_length=1)
    finding_ids: list[str] = Field(default_factory=list)
    positions: list[Position] = Field(default_factory=list)
    resolution: Resolution
    rationale: str = Field(min_length=1)


class AcceptedRisk(_Lenient):
    finding_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class FinalFinding(AgentFinding):
    merged_from: list[str] = Field(min_length=1, description="Ids of the raised findings this one consolidates.")
    decision: str = Field(min_length=1, description="Moderator rationale for the final severity and priority.")


class ModeratorOutput(_Lenient):
    summary: str = Field(min_length=1)
    findings: list[FinalFinding] = Field(default_factory=list)
    disagreements: list[Disagreement] = Field(default_factory=list)
    accepted_risks: list[AcceptedRisk] = Field(default_factory=list)


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``$ref``/``$defs`` so the schema is self-contained (what Converse tool specs expect)."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    resolved: dict[str, Any] = walk(schema)
    return resolved


def tool_schema(model: type[BaseModel]) -> dict[str, Any]:
    return inline_refs(model.model_json_schema())
