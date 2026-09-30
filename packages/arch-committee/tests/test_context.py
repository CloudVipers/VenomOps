from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from findings_schema import validate
from typer.testing import CliRunner

from arch_committee import cli
from arch_committee.context import MAX_CONTEXT_FINDINGS, ContextError, load_context_findings, render_context
from arch_committee.orchestrator import CONTEXT_INSTRUCTIONS, run_committee
from arch_committee.plan_parser import load_plan
from arch_committee.report import write_outputs

from .conftest import FakeCommittee, plan_path

runner = CliRunner()


def prior(fid: str = "KD-K8S-002", name: str = "oom", severity: str = "high", **over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": fid, "schema_version": "1.0.0", "source": "kdoctor", "severity": severity,
        "title": f"OOMKilled en el container app ({name})",
        "resource": {"type": "Pod", "name": name, "namespace": "kdoctor-demo"},
        "evidence": [{"kind": "last-state", "detail": "Terminated reason=OOMKilled, restartCount=6"},
                     {"kind": "limits", "detail": "limits.memory=32Mi"}],
        "root_cause": "El contenedor superó su límite de memoria de 32Mi.",
        "suggested_fix": {"summary": "Subir limits.memory", "steps": ["Subir a 64Mi"]},
        "risk_of_fix": "low", "detected_at": "2026-09-30T12:00:00Z",
    }  # fmt: skip
    doc.update(over)
    return doc


def write(tmp_path: Path, name: str, data: Any) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


K8S_PLAN = "k8s-oom"


def test_prior_findings_reach_round_one_and_the_moderator_but_not_the_rebuttal(tmp_path: Path) -> None:
    fake = FakeCommittee()
    ctx = load_context_findings([write(tmp_path, "f.json", [prior()])])
    run_committee(load_plan(plan_path(K8S_PLAN)), fake, context=ctx)
    with_ctx = {c["tool"] for c in fake.calls if "<prior_findings>" in c["user"]}
    assert with_ctx == {"report_findings", "finalize"}
    round2 = [c for c in fake.calls if c["tool"] == "respond_to_findings"]
    assert round2 and all("<prior_findings>" not in c["user"] for c in round2)
    assert all(CONTEXT_INSTRUCTIONS in c["user"] for c in fake.calls if "<prior_findings>" in c["user"])


def test_without_context_nothing_is_sent_about_it() -> None:
    fake = FakeCommittee()
    run_committee(load_plan(plan_path(K8S_PLAN)), fake)
    assert all("prior_findings" not in c["user"] for c in fake.calls)


def test_the_committee_links_a_runtime_finding_to_the_plan_resource(tmp_path: Path) -> None:
    ctx = load_context_findings([write(tmp_path, "f.json", [prior()])])
    result = run_committee(load_plan(plan_path(K8S_PLAN)), FakeCommittee(), context=ctx)
    linked = [f for f in result.final if f.resource == "kubernetes_pod_v1.oom"]
    assert linked and linked[0].severity == "high"
    assert any(e.kind == "prior-finding" and "KD-K8S-002" in e.detail for e in linked[0].evidence)
    assert "Confirmado por un finding previo" in linked[0].decision
    # the same plan without the runtime observation produces no such finding
    bare = run_committee(load_plan(plan_path(K8S_PLAN)), FakeCommittee())
    assert not [f for f in bare.final if "OOMKilled" in f.title]


def test_a_prior_finding_about_another_resource_is_not_forced_onto_the_plan(tmp_path: Path) -> None:
    ctx = load_context_findings([write(tmp_path, "f.json", [prior(name="something-else")])])
    result = run_committee(load_plan(plan_path(K8S_PLAN)), FakeCommittee(), context=ctx)
    assert not [f for f in result.final if "OOMKilled" in f.title]


def test_context_is_redacted_before_it_reaches_the_model(tmp_path: Path) -> None:
    secret = prior(evidence=[{"kind": "log-tail", "detail": "connecting with password=hunter2 to acct 123456789012"}])
    fake = FakeCommittee()
    run_committee(
        load_plan(plan_path(K8S_PLAN)), fake, context=load_context_findings([write(tmp_path, "f.json", [secret])])
    )
    sent = "\n".join(c["user"] for c in fake.calls)
    assert "hunter2" not in sent and "123456789012" not in sent and "prior_findings" in sent


def test_the_report_lists_the_context_and_the_findings_still_validate(tmp_path: Path) -> None:
    ctx = load_context_findings([write(tmp_path, "f.json", [prior()])])
    result = run_committee(load_plan(plan_path(K8S_PLAN)), FakeCommittee(), context=ctx)
    report, findings_path, warnings = write_outputs(result, tmp_path / "out", "m")
    text = report.read_text(encoding="utf-8")
    assert "## Contexto previo" in text and "KD-K8S-002" in text and "kdoctor" in text and not warnings
    assert all(validate(d) == [] for d in json.loads(findings_path.read_text()))
    assert (
        "## Contexto previo"
        not in write_outputs(run_committee(load_plan(plan_path(K8S_PLAN)), FakeCommittee()), tmp_path / "o2", "m")[
            0
        ].read_text()
    )


# ---- loading and rendering --------------------------------------------------------------------


def test_one_finding_an_array_and_several_files_are_accepted(tmp_path: Path) -> None:
    a = write(tmp_path, "a.json", prior("KD-K8S-001", "a"))
    b = write(tmp_path, "b.json", [prior("KD-K8S-002", "b"), prior("KD-K8S-003", "c")])
    assert [f.id for f in load_context_findings([a, b])] == ["KD-K8S-001", "KD-K8S-002", "KD-K8S-003"]
    assert load_context_findings([]) == []


def test_bad_context_files_are_rejected_with_a_clear_message(tmp_path: Path) -> None:
    with pytest.raises(ContextError, match="No such file|nope"):
        load_context_findings([tmp_path / "nope.json"])
    broken = tmp_path / "x.json"
    broken.write_text("{oops")
    with pytest.raises(ContextError, match="not valid JSON"):
        load_context_findings([broken])
    with pytest.raises(ContextError, match=r"\[1\].*findings-schema"):
        load_context_findings([write(tmp_path, "y.json", [prior(), {"id": "x"}])])


def test_rendering_is_ordered_by_severity_and_capped(tmp_path: Path) -> None:
    docs = [prior(f"KD-{i:03d}", f"p{i}", severity="low") for i in range(MAX_CONTEXT_FINDINGS + 10)] + [
        prior("KD-CRIT", "boss", severity="critical")
    ]
    findings = load_context_findings([write(tmp_path, "many.json", docs)])
    text, truncated = render_context(findings)
    items = json.loads(text)
    assert truncated and len(items) <= MAX_CONTEXT_FINDINGS and items[0]["id"] == "KD-CRIT"
    tiny, cut = render_context(findings, max_chars=600)
    assert cut and len(tiny) <= 600 and json.loads(tiny)[0]["id"] == "KD-CRIT"
    assert render_context([]) == ("[]", False)


def test_a_huge_context_is_reported_as_a_note(tmp_path: Path) -> None:
    docs = [prior(f"KD-{i:03d}", f"p{i}") for i in range(MAX_CONTEXT_FINDINGS + 5)]
    result = run_committee(
        load_plan(plan_path(K8S_PLAN)),
        FakeCommittee(),
        context=load_context_findings([write(tmp_path, "m.json", docs)]),
    )
    assert any("findings previos" in n for n in result.notes)


# ---- CLI --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeCommittee:
    fake = FakeCommittee()
    monkeypatch.setattr(cli, "LLM_FACTORY", lambda model, region: fake)
    return fake


def test_the_cli_accepts_context_and_shows_it_in_the_dry_run(tmp_path: Path) -> None:
    f1, f2 = write(tmp_path, "1.json", [prior()]), write(tmp_path, "2.json", prior("KD-K8S-009", "x"))
    result = runner.invoke(
        cli.app,
        [
            "review",
            "--plan",
            str(plan_path(K8S_PLAN)),
            "--dry-run",
            "--context-findings",
            str(f1),
            "--context-findings",
            str(f2),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Context: 2 prior findings (kdoctor)" in result.output
    assert "Redacted prior findings that agents would receive" in result.output and "KD-K8S-009" in result.output


def test_the_cli_runs_with_context_and_links_the_finding(tmp_path: Path) -> None:
    src = write(tmp_path, "f.json", [prior()])
    out = tmp_path / "out"
    result = runner.invoke(
        cli.app,
        [
            "review",
            "--plan",
            str(plan_path(K8S_PLAN)),
            "--out",
            str(out),
            "--model-id",
            "m",
            "--context-findings",
            str(src),
        ],
    )
    assert result.exit_code == 0, result.output
    docs = json.loads((out / "findings.json").read_text())
    linked = [d for d in docs if d["resource"] == {"type": "kubernetes_pod_v1", "name": "oom"}]
    assert linked and any(e["kind"] == "prior-finding" for e in linked[0]["evidence"])


def test_the_cli_rejects_a_bad_context_file(tmp_path: Path) -> None:
    bad = write(tmp_path, "bad.json", [{"id": "x"}])
    result = runner.invoke(
        cli.app, ["review", "--plan", str(plan_path(K8S_PLAN)), "--dry-run", "--context-findings", str(bad)]
    )
    assert result.exit_code == 2 and "findings-schema" in result.output
