"""Typed (pydantic v2) view of a finding. Equivalent to the Go structs in ``go/finding.go``."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .validator import validate_or_raise


class Source(StrEnum):
    KDOCTOR = "kdoctor"
    PR_AGENT = "pr-agent"
    ARCH_COMMITTEE = "arch-committee"
    MANUAL = "manual"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Resource(_Model):
    type: str
    name: str
    namespace: str | None = None
    region: str | None = None
    account_alias: str | None = None
    path: str | None = None


class Evidence(_Model):
    kind: str
    detail: str


class SuggestedFix(_Model):
    summary: str
    steps: list[str]
    iac_hint: str | None = None


class Finding(_Model):
    id: str
    schema_version: str
    source: Source
    severity: Severity
    title: str
    resource: Resource
    evidence: list[Evidence]
    root_cause: str
    suggested_fix: SuggestedFix
    risk_of_fix: Risk
    references: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    detected_at: datetime

    @classmethod
    def from_dict(cls, data: Any) -> Finding:
        """Validate against the JSON Schema first (the contract), then build the typed model."""
        validate_or_raise(data)
        return cls.model_validate(data)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict that validates against the schema again (round trip)."""
        return self.model_dump(mode="json", exclude_none=True)
