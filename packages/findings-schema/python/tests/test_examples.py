from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from findings_schema import (
    Finding,
    FindingValidationError,
    load_schema,
    validate,
    validate_json,
    validate_or_raise,
)

from .conftest import example_files

VALID = example_files("valid")
INVALID = example_files("invalid")


def test_there_are_enough_examples() -> None:
    assert len(VALID) >= 5
    assert len(INVALID) >= 5


def test_schema_is_valid_draft_2020_12() -> None:
    schema = load_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_valid_examples_pass(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert validate(data) == []
    validate_or_raise(data)


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_valid_examples_round_trip_through_the_model(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    finding = Finding.from_dict(data)
    assert validate(finding.to_dict()) == []


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_invalid_examples_fail_with_the_expected_error(
    path: Path, expected_errors: dict[str, dict[str, str]]
) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    issues = validate(data)
    assert issues, f"{path.name} should be invalid"
    expected = expected_errors[path.name]
    assert (expected["path"], expected["keyword"]) in {(i.path, i.keyword) for i in issues}


def test_every_invalid_example_has_an_expectation(
    expected_errors: dict[str, dict[str, str]],
) -> None:
    assert {p.name for p in INVALID} == set(expected_errors)


def test_error_messages_point_to_the_field() -> None:
    issues = validate_json(json.dumps({"id": "kd-1"}))
    rendered = "\n".join(str(i) for i in issues)
    assert "/id" in rendered
    assert "<root>" in rendered  # missing required properties are reported at the root


def test_validate_or_raise_reports_all_issues() -> None:
    with pytest.raises(FindingValidationError) as exc:
        validate_or_raise({"id": "kd-1", "severity": "urgent"})
    assert len(exc.value.issues) >= 3
    assert "invalid finding" in str(exc.value)


def test_malformed_json_is_an_issue_not_a_crash() -> None:
    issues = validate_json("{not json")
    assert len(issues) == 1 and issues[0].keyword == "json"


def test_from_dict_rejects_invalid_before_building_the_model() -> None:
    with pytest.raises(FindingValidationError):
        Finding.from_dict({"id": "x"})
