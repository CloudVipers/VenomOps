from __future__ import annotations

import ast
import inspect

import pytest

from pr_agent import github_client, pr_body
from pr_agent.fixes import FixOutcome
from pr_agent.github_client import PyGithubClient, parse_remote
from pr_agent.redact import redact
from pr_agent.safety import CommandResult

from .conftest import load_finding

OUTCOME = FixOutcome(summary="Habilitar cifrado.", details=["detalle"], risk="medium", files=frozenset({"main.tf"}))
OK = CommandResult(("terraform", "validate"), 0, "Success!", "")


def test_body_redacts_secrets_from_plan_and_evidence() -> None:
    plan = CommandResult(("terraform", "plan"), 0, "arn:aws:iam::123456789012:role/x password=hunter2", "")
    body = pr_body.build(load_finding("s3-no-encryption.json"), OUTCOME, "diff", OK, plan, None)
    assert "hunter2" not in body and "123456789012" not in body
    assert "[ACCOUNT-ID]" in body and "🟡 Medio" in body


def test_long_plans_are_truncated_inside_a_collapsible_section() -> None:
    plan = CommandResult(("terraform", "plan"), 0, "x" * (pr_body.MAX_PLAN_CHARS + 500), "")
    body = pr_body.build(load_finding("s3-no-encryption.json"), OUTCOME, "diff", OK, plan, None)
    assert "<details>" in body and "salida truncada" in body


def test_missing_plan_is_disclosed_with_the_reason() -> None:
    body = pr_body.build(load_finding("s3-no-encryption.json"), OUTCOME, "diff", OK, None, "omitido con --skip-plan")
    assert "no disponible" in body and "--skip-plan" in body


def test_a_diff_containing_code_fences_does_not_break_the_body() -> None:
    body = pr_body.build(load_finding("s3-no-encryption.json"), OUTCOME, "+```\n+x", OK, None, "n/a")
    assert "````diff" in body


def test_title_is_a_conventional_commit_subject() -> None:
    title = pr_body.title(load_finding("s3-no-encryption.json"), OUTCOME)
    assert title == "fix(terraform): Habilitar cifrado [TF-S3-001]"


@pytest.mark.parametrize(
    ("url", "slug"),
    [
        ("https://github.com/acme/infra.git", "acme/infra"),
        ("https://github.com/acme/infra", "acme/infra"),
        ("git@github.com:acme/infra.git", "acme/infra"),
        ("https://token@github.com/acme/my.repo.git\n", "acme/my.repo"),
    ],
)
def test_parse_remote(url: str, slug: str) -> None:
    assert parse_remote(url) == slug


def test_parse_remote_rejects_non_github_remotes() -> None:
    with pytest.raises(ValueError, match="--github-repo"):
        parse_remote("/srv/git/remote.git")


def test_the_github_client_can_open_prs_but_never_merge() -> None:
    public = {n for n, _ in inspect.getmembers(PyGithubClient, inspect.isfunction) if not n.startswith("_")}
    assert public == {"default_branch", "open_pull_request"}
    # Look at real identifiers (function names, attribute accesses), not at docstrings or comments.
    tree = ast.parse(inspect.getsource(github_client))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    forbidden = ("merge", "approve", "close", "delete", "edit", "review", "dismiss", "push", "update_branch")
    assert not [n for n in names if any(word in n.lower() for word in forbidden)], names


def test_the_github_client_needs_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        PyGithubClient()


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("key AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
        ("DB_PASSWORD=s3cr3t!", "s3cr3t!"),
        ("Authorization: Bearer abcdefghijklmnop", "abcdefghijklmnop"),
        ("https://user:pa55@example.com/x", "pa55"),
        ("acct 123456789012", "123456789012"),
        ("-----BEGIN " + "RSA PRIVATE KEY-----\nMIIBOg\n-----END " + "RSA PRIVATE KEY-----", "MIIBOg"),
    ],
)
def test_redact(text: str, gone: str) -> None:
    assert gone not in redact(text)


def test_redact_keeps_harmless_text() -> None:
    assert redact("Plan: 1 to add, 0 to change") == "Plan: 1 to add, 0 to change"
