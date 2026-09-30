"""Validate findings against the shared JSON Schema (single source of truth).

The schema lives in ``packages/findings-schema/schema/finding.schema.json``. Go and Python both
validate against that same file, so a document is valid in one language if and only if it is valid
in the other (see the parity test).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

# Installed wheel: the schema is copied next to this module (see [tool.hatch.build] in pyproject.toml).
# Source checkout: python/findings_schema/validator.py -> python -> findings-schema/schema/.
_PACKAGED_SCHEMA = Path(__file__).resolve().parent / "finding.schema.json"
_SOURCE_SCHEMA = Path(__file__).resolve().parents[2] / "schema" / "finding.schema.json"
SCHEMA_PATH = _PACKAGED_SCHEMA if _PACKAGED_SCHEMA.is_file() else _SOURCE_SCHEMA


@dataclass(frozen=True, order=True)
class ValidationIssue:
    """One schema violation. ``path`` is a JSON Pointer ("" is the document root)."""

    path: str
    keyword: str
    message: str

    def __str__(self) -> str:
        return f"{self.path or '<root>'}: {self.message} [{self.keyword}]"


class FindingValidationError(ValueError):
    """Raised by :func:`validate_or_raise` with every issue found."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__("invalid finding:\n" + "\n".join(f"  - {i}" for i in issues))


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as fh:
        schema: dict[str, Any] = json.load(fh)
    return schema


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _pointer(parts: Any) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def validate(data: Any) -> list[ValidationIssue]:
    """Return every schema violation (empty list means the finding is valid)."""
    issues = [
        ValidationIssue(_pointer(err.absolute_path), str(err.validator), err.message)
        for err in _validator().iter_errors(data)
    ]
    return sorted(issues)


def validate_json(text: str) -> list[ValidationIssue]:
    """Like :func:`validate` but takes raw JSON text; malformed JSON is reported as an issue."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [ValidationIssue("", "json", f"malformed JSON: {exc.msg} (line {exc.lineno})")]
    return validate(data)


def validate_or_raise(data: Any) -> None:
    issues = validate(data)
    if issues:
        raise FindingValidationError(issues)
