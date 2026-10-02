"""venom-doctor -> pr-agent, using the REAL output of `venom-doctor -n venom-demo -o json` captured from a kind cluster
(examples/findings/venom-doctor-output.json). If venom-doctor's JSON contract drifts, these tests break."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from findings_schema import Finding
from typer.testing import CliRunner

from pr_agent import cli
from pr_agent.safety import SafeRunner

from .conftest import EXAMPLES, copy_example

runner = CliRunner()
VENOM_DOCTOR_OUTPUT = EXAMPLES / "findings" / "venom-doctor-output.json"


@pytest.fixture(autouse=True)
def fake_terraform(monkeypatch: pytest.MonkeyPatch, runner_factory: Callable[[Path], SafeRunner]) -> None:
    monkeypatch.setattr(cli, "RUNNER_FACTORY", runner_factory)


def test_the_captured_venom_doctor_output_satisfies_the_shared_contract() -> None:
    docs = json.loads(VENOM_DOCTOR_OUTPUT.read_text(encoding="utf-8"))
    findings = [Finding.from_dict(d) for d in docs]
    assert [f.id for f in findings] == ["KD-K8S-001", "KD-K8S-002", "KD-K8S-003", "KD-K8S-004"]
    assert all(f.source.value == "venom-doctor" and f.resource.namespace == "venom-demo" for f in findings)


def test_venom_doctor_oom_finding_carries_the_evidence_pr_agent_needs() -> None:
    oom = next(d for d in json.loads(VENOM_DOCTOR_OUTPUT.read_text()) if d["id"] == "KD-K8S-002")
    change = next(e for e in oom["evidence"] if e["kind"] == "memory-limit-change")
    assert change["detail"] == "container=app;from=32Mi;to=64Mi"


def test_the_pipe_turns_a_real_venom_doctor_array_into_a_minimal_terraform_fix(tmp_path: Path) -> None:
    repo = copy_example("k8s-oom-demo", tmp_path)
    result = runner.invoke(
        cli.app, ["fix", "--finding", str(VENOM_DOCTOR_OUTPUT), "--supported", "--repo", str(repo), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    diff = result.output.split("--- PR title ---")[0].splitlines()
    changed = [ln for ln in diff if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]
    assert changed == ['-          memory = "32Mi"', '+          memory = "64Mi"']
    assert "Pod venom-demo/oom" in result.output and "kubernetes_pod_v1.oom" in result.output
    assert "Checklist de revisión humana" in result.output
    assert 'memory = "32Mi"' in (repo / "main.tf").read_text()  # dry-run: the repo is untouched


def test_venom_doctor_json_can_be_piped_through_stdin(tmp_path: Path) -> None:
    repo = copy_example("k8s-oom-demo", tmp_path)
    result = runner.invoke(
        cli.app,
        ["fix", "--finding", "-", "--resource", "venom-demo/oom", "--repo", str(repo), "--dry-run"],
        input=VENOM_DOCTOR_OUTPUT.read_text(),
    )
    assert result.exit_code == 0 and "KD-K8S-002" in result.output


@pytest.mark.parametrize("finding_id", ["KD-K8S-001", "KD-K8S-003", "KD-K8S-004"])
def test_the_other_venom_doctor_findings_are_not_auto_fixable_and_say_so(tmp_path: Path, finding_id: str) -> None:
    repo = copy_example("k8s-oom-demo", tmp_path)
    result = runner.invoke(
        cli.app, ["fix", "--finding", str(VENOM_DOCTOR_OUTPUT), "--id", finding_id, "--repo", str(repo), "--dry-run"]
    )
    assert result.exit_code == 1 and "no fixer" in result.output and "--agent" in result.output
    assert 'memory = "32Mi"' in (repo / "main.tf").read_text()


def test_a_venom_doctor_oom_for_a_pod_without_terraform_is_refused_not_guessed(tmp_path: Path) -> None:
    docs = json.loads(VENOM_DOCTOR_OUTPUT.read_text())
    docs[1]["resource"]["name"] = "unmanaged"
    f = tmp_path / "f.json"
    f.write_text(json.dumps(docs))
    repo = copy_example("k8s-oom-demo", tmp_path)
    result = runner.invoke(cli.app, ["fix", "--finding", str(f), "--supported", "--repo", str(repo), "--dry-run"])
    assert result.exit_code == 1 and "no kubernetes_* workload" in result.output
