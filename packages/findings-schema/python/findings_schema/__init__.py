"""VenomOps findings schema: pydantic models and validator."""

from .models import Evidence, Finding, Resource, Risk, Severity, Source, SuggestedFix
from .validator import (
    SCHEMA_PATH,
    FindingValidationError,
    ValidationIssue,
    load_schema,
    validate,
    validate_json,
    validate_or_raise,
)

__all__ = [
    "SCHEMA_PATH",
    "Evidence",
    "Finding",
    "FindingValidationError",
    "Resource",
    "Risk",
    "Severity",
    "Source",
    "SuggestedFix",
    "ValidationIssue",
    "load_schema",
    "validate",
    "validate_json",
    "validate_or_raise",
]

__version__ = "1.0.0"
