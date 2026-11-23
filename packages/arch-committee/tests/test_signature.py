"""arch-committee signs findings.json when the shared key is set (ADR 0012)."""

from __future__ import annotations

from pathlib import Path

import pytest
from findings_schema.signature import ENV_KEY, sidecar, verify_bytes
from typer.testing import CliRunner

from arch_committee import cli

from .conftest import FakeCommittee, plan_path

KEY = "test-key-0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "LLM_FACTORY", lambda model, region: FakeCommittee())


def review(out: Path) -> tuple[int, str]:
    args = ["review", "--plan", str(plan_path("public-bucket")), "--out", str(out), "--model-id", "m"]
    result = CliRunner().invoke(cli.app, args)
    return result.exit_code, result.output


def test_findings_are_signed_with_the_shared_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_KEY, KEY)
    code, output = review(tmp_path)
    assert code == 0, output
    findings = tmp_path / "findings.json"
    assert "Signed:" in output and sidecar(findings).exists()
    verify_bytes(findings.read_bytes(), sidecar(findings), KEY.encode())


def test_no_key_no_signature(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_KEY, raising=False)
    code, output = review(tmp_path)
    assert code == 0 and not sidecar(tmp_path / "findings.json").exists()


def test_a_short_key_is_a_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_KEY, "short")
    code, output = review(tmp_path)
    assert code == 2 and "at least 32" in output
