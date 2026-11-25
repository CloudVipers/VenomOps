"""pr-agent verifies the detached HMAC signature of a finding (ADR 0012)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from findings_schema.signature import ENV_KEY, write_signature
from typer.testing import CliRunner

from pr_agent import cli

from .conftest import EXAMPLES

KEY = "test-key-0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def fake_runner(monkeypatch: pytest.MonkeyPatch, runner_factory: object) -> None:
    monkeypatch.setattr(cli, "RUNNER_FACTORY", runner_factory)


@pytest.fixture
def finding(tmp_path: Path) -> Path:
    path = tmp_path / "finding.json"
    shutil.copy(EXAMPLES / "findings" / "s3-no-encryption.json", path)
    return path


def fix(finding: Path, repo: Path, *extra: str, stdin: bytes | None = None) -> tuple[int, str]:
    source = "-" if stdin is not None else str(finding)
    result = CliRunner().invoke(
        cli.app, ["fix", "--finding", source, "--repo", str(repo), "--dry-run", *extra], input=stdin
    )
    return result.exit_code, result.output


def test_a_valid_signature_is_accepted(finding: Path, s3_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_KEY, KEY)
    write_signature(finding, KEY.encode())
    code, out = fix(finding, s3_repo, "--require-signature")
    assert code == 0, out


def test_a_tampered_finding_is_refused_even_without_the_flag(
    finding: Path, s3_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_KEY, KEY)
    write_signature(finding, KEY.encode())
    finding.write_text(finding.read_text().replace('"high"', '"low"'))
    code, out = fix(finding, s3_repo)
    assert code == 2 and "modified" in out


def test_require_signature_needs_a_key_and_a_signature(
    finding: Path, s3_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_KEY, raising=False)
    code, out = fix(finding, s3_repo, "--require-signature")
    assert code == 2 and ENV_KEY in out
    monkeypatch.setenv(ENV_KEY, KEY)
    code, out = fix(finding, s3_repo, "--require-signature")
    assert code == 2 and "not found" in out


def test_stdin_with_an_explicit_signature(finding: Path, s3_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_KEY, KEY)
    sig = write_signature(finding, KEY.encode())
    data = finding.read_bytes()
    code, out = fix(finding, s3_repo, "--require-signature", stdin=data)
    assert code == 2 and "--signature" in out
    code, out = fix(finding, s3_repo, "--require-signature", "--signature", str(sig), stdin=data)
    assert code == 0, out


def test_unsigned_findings_still_work_by_default(finding: Path, s3_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_KEY, raising=False)
    code, out = fix(finding, s3_repo)
    assert code == 0, out
