from __future__ import annotations

from pathlib import Path

import pytest

from pr_agent import hcl
from pr_agent.fixes import AlreadyFixedError, FixerError, get_fixer
from pr_agent.fixes.k8s_memory import K8sMemoryLimitFixer, parse_change
from pr_agent.safety import SafeRunner
from pr_agent.tools import ToolBox, ToolError
from pr_agent.workflow import FixOptions, run_fix

from .conftest import copy_example, make_finding

DEPLOYMENT = """resource "kubernetes_deployment_v1" "api" {
  metadata {
    name      = "payments-api"
    namespace = "payments"
  }

  spec {
    template {
      spec {
        container {
          name  = "sidecar"
          image = "busybox"
          resources {
            limits = {
              memory = "64Mi"
            }
          }
        }
        container {
          name  = "app"
          image = "example.com/app:1"
          resources {
            requests = {
              memory = "64Mi"
            }
            limits {
              memory = "64Mi"
              cpu    = "500m"
            }
          }
        }
      }
    }
  }
}
"""


def oom(pod: str = "oom", namespace: str = "kdoctor-demo", change: str | None = "container=app;from=32Mi;to=64Mi"):  # type: ignore[no-untyped-def]
    evidence = [{"kind": "last-state", "detail": 'container "app" Terminated reason=OOMKilled'}]
    if change:
        evidence.append({"kind": "memory-limit-change", "detail": change})
    return make_finding(
        id="KD-K8S-002",
        source="kdoctor",
        resource={"type": "Pod", "name": pod, "namespace": namespace},
        evidence=evidence,
    )


def tools_for(tmp_path: Path, tf: str | None = None) -> ToolBox:
    repo = copy_example("k8s-oom-demo", tmp_path)
    if tf is not None:
        (repo / "main.tf").write_text(tf)
    return ToolBox(repo, SafeRunner(repo))


def limit_of(text: str, rtype: str, rname: str) -> str:
    block = hcl.find_resource(text, rtype, rname)
    assert block is not None
    return text[block.start : block.end]


# ---- the HCL operation ------------------------------------------------------------------------


def test_the_limit_of_the_named_container_changes_and_nothing_else_does() -> None:
    block = hcl.find_resource(DEPLOYMENT, "kubernetes_deployment_v1", "api")
    assert block is not None
    new, detail = hcl.set_memory_limit(DEPLOYMENT, block, "app", "64Mi", "128Mi")
    hcl.parse(new)
    assert detail == "app: limits.memory 64Mi -> 128Mi"
    assert (
        new.count('memory = "128Mi"') == 1 and new.count('memory = "64Mi"') == 2
    )  # sidecar limit + app requests survive
    app = new[new.index('name  = "app"') :]
    assert 'requests = {\n              memory = "64Mi"' in app  # requests untouched
    assert 'memory = "128Mi"\n              cpu' in app  # the block-style limits was the one edited


def test_both_limits_syntaxes_are_supported() -> None:
    map_style = (
        'resource "kubernetes_pod_v1" "p" {\n  spec {\n    container {\n      name = "c"\n      resources {\n'
        '        limits = {\n          memory = "1Gi"\n        }\n      }\n    }\n  }\n}\n'
    )
    block = hcl.find_resource(map_style, "kubernetes_pod_v1", "p")
    assert block is not None
    assert 'memory = "2Gi"' in hcl.set_memory_limit(map_style, block, "c", "1Gi", "2Gi")[0]


@pytest.mark.parametrize(
    ("container", "old", "new", "message"),
    [
        ("ghost", "64Mi", "128Mi", r"not found in the resource \(available"),
        ("app", "999Mi", "256Mi", 'has no memory = "999Mi"'),
        ("app", "64Mi", "128Mi", "already 128Mi"),  # the limit is already 128Mi: nothing to do
    ],
)
def test_the_operation_refuses_what_it_cannot_do_safely(container: str, old: str, new: str, message: str) -> None:
    text = DEPLOYMENT.replace('limits {\n              memory = "64Mi"', 'limits {\n              memory = "128Mi"')
    block = hcl.find_resource(text, "kubernetes_deployment_v1", "api")
    assert block is not None
    with pytest.raises(hcl.EditError, match=message):
        hcl.set_memory_limit(text, block, container, old, new)


def test_a_container_without_limits_or_declared_twice_is_refused() -> None:
    nolimits = 'resource "kubernetes_pod_v1" "p" {\n  spec {\n    container {\n      name = "c"\n    }\n  }\n}\n'
    block = hcl.find_resource(nolimits, "kubernetes_pod_v1", "p")
    assert block is not None
    with pytest.raises(hcl.EditError, match="0 'limits'"):
        hcl.set_memory_limit(nolimits, block, "c", "1Mi", "2Mi")
    twice = nolimits.replace("  }\n}\n", '    container {\n      name = "c"\n    }\n  }\n}\n')
    block = hcl.find_resource(twice, "kubernetes_pod_v1", "p")
    assert block is not None
    with pytest.raises(hcl.EditError, match="more than once"):
        hcl.set_memory_limit(twice, block, "c", "1Mi", "2Mi")


def test_the_toolbox_op_only_accepts_kubernetes_resources_and_valid_quantities(tmp_path: Path) -> None:
    tools = tools_for(tmp_path)
    base = {
        "op": "set_memory_limit",
        "resource": "kubernetes_pod_v1.oom",
        "container": "app",
        "old": "32Mi",
        "new": "64Mi",
    }
    assert tools.edit_hcl("main.tf", base) and 'memory = "64Mi"' in tools.read_file("main.tf")
    with pytest.raises(ToolError, match="only applies to kubernetes_"):
        tools.edit_hcl("main.tf", {**base, "resource": "aws_s3_bucket.x"})
    for bad in ("lots", "32 Mi", "", "1e3Mi"):
        with pytest.raises(ToolError, match="quantities"):
            tools.edit_hcl("main.tf", {**base, "old": "64Mi", "new": bad})


# ---- the fixer --------------------------------------------------------------------------------


def test_the_registry_knows_the_kdoctor_finding() -> None:
    assert isinstance(get_fixer("KD-K8S-002"), K8sMemoryLimitFixer)


def test_a_pod_oom_finding_raises_the_limit_in_its_terraform(tmp_path: Path) -> None:
    tools = tools_for(tmp_path)
    outcome = K8sMemoryLimitFixer().apply(oom(), tools)
    text = tools.read_file("main.tf")
    assert 'limits = {\n          memory = "64Mi"' in text and 'requests = {\n          memory = "16Mi"' in text
    hcl.parse(text)
    assert outcome.files == {"main.tf"} and outcome.risk == "low"
    assert "32Mi a 64Mi" in outcome.summary and any("OOMKilled" in d for d in outcome.details)


def test_the_fixer_is_idempotent(tmp_path: Path) -> None:
    tools = tools_for(tmp_path)
    K8sMemoryLimitFixer().apply(oom(), tools)
    with pytest.raises(AlreadyFixedError):
        K8sMemoryLimitFixer().apply(oom(), tools)


@pytest.mark.parametrize(
    ("pod", "namespace", "matches"),
    [
        ("payments-api-7d9f8b6c5d-x2k4p", "payments", True),  # Deployment -> ReplicaSet hash -> pod hash
        ("payments-api-7d9f8b6c5d-x2k4p", "other", False),  # same name, different namespace
        ("payments-api", "payments", False),  # not a generated pod name
        ("payments-apix-7d9f8b6c5d-x2k4p", "payments", False),  # a different deployment with a similar prefix
    ],
)
def test_deployment_pods_are_mapped_back_to_their_deployment(
    tmp_path: Path, pod: str, namespace: str, matches: bool
) -> None:
    tools = tools_for(tmp_path, DEPLOYMENT)
    finding = oom(pod=pod, namespace=namespace, change="container=app;from=64Mi;to=128Mi")
    if matches:
        K8sMemoryLimitFixer().apply(finding, tools)
        assert 'memory = "128Mi"' in tools.read_file("main.tf")
    else:
        with pytest.raises(FixerError, match="no kubernetes_"):
            K8sMemoryLimitFixer().apply(finding, tools)


def test_statefulset_pods_match_by_ordinal(tmp_path: Path) -> None:
    sts = DEPLOYMENT.replace("kubernetes_deployment_v1", "kubernetes_stateful_set_v1").replace("payments-api", "db")
    tools = tools_for(tmp_path, sts)
    K8sMemoryLimitFixer().apply(oom(pod="db-0", namespace="payments", change="container=app;from=64Mi;to=256Mi"), tools)
    assert 'memory = "256Mi"' in tools.read_file("main.tf")


def test_ambiguous_or_non_literal_workloads_are_not_guessed(tmp_path: Path) -> None:
    two = DEPLOYMENT + DEPLOYMENT.replace('"api"', '"api_copy"')
    with pytest.raises(FixerError, match="several Terraform workloads"):
        K8sMemoryLimitFixer().apply(
            oom(pod="payments-api-7d9f8b6c5d-x2k4p", namespace="payments", change="container=app;from=64Mi;to=1Gi"),
            tools_for(tmp_path, two),
        )
    dynamic = DEPLOYMENT.replace('name      = "payments-api"', "name      = var.name")
    with pytest.raises(FixerError, match="no kubernetes_"):
        K8sMemoryLimitFixer().apply(
            oom(pod="payments-api-7d9f8b6c5d-x2k4p", namespace="payments", change="container=app;from=64Mi;to=1Gi"),
            tools_for(tmp_path / "b", dynamic),
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (None, "no 'memory-limit-change' evidence"),
        ("container=app;from=lots;to=64Mi", "malformed"),
        ("container=;from=32Mi;to=64Mi", "malformed"),
        ("from=32Mi;to=64Mi", "malformed"),
    ],
)
def test_the_evidence_contract_is_enforced(change: str | None, message: str) -> None:
    with pytest.raises(FixerError, match=message):
        parse_change(oom(change=change))


def test_a_wrong_container_name_reports_the_available_ones(tmp_path: Path) -> None:
    with pytest.raises(FixerError, match="available"):
        K8sMemoryLimitFixer().apply(oom(change="container=ghost;from=32Mi;to=64Mi"), tools_for(tmp_path))


def test_the_workflow_produces_a_minimal_dry_run_diff(tmp_path: Path, runner_factory) -> None:  # type: ignore[no-untyped-def]
    repo = copy_example("k8s-oom-demo", tmp_path)
    report = run_fix(oom(), FixOptions(repo=repo, dry_run=True), runner_factory=runner_factory)
    assert report.branch == "fix/kd-k8s-002-oom" and list(report.changed_files) == ["main.tf"]
    changed = [ln for ln in report.diff.splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---")]
    assert changed == ['-          memory = "32Mi"', '+          memory = "64Mi"']
