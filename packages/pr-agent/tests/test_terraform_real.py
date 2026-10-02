"""End-to-end with the real terraform binary (downloads the AWS provider; set TF_PLUGIN_CACHE_DIR to reuse it)."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from findings_schema import Finding

from pr_agent.safety import SafeRunner
from pr_agent.workflow import FixOptions, run_fix, snapshot

from .conftest import copy_example, load_finding

pytestmark = [
    pytest.mark.terraform,
    pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform is not installed"),
    pytest.mark.skipif(os.environ.get("PR_AGENT_SKIP_TERRAFORM") == "1", reason="PR_AGENT_SKIP_TERRAFORM=1"),
]


@pytest.fixture(autouse=True)
def offline_placeholder_credentials(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`terraform plan` needs *some* credentials even though the demo config never calls AWS."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "placeholder")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "placeholder")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    yield


@pytest.mark.parametrize(
    ("example", "finding", "needle"),
    [
        ("s3-demo", "s3-no-encryption.json", "aws_s3_bucket_server_side_encryption_configuration"),
        ("ebs-demo", "ebs-gp2.json", 'type              = "gp3"'),
        ("tags-demo", "missing-tags.json", 'Owner = "platform-team"'),
    ],
)
def test_dry_run_passes_real_terraform_validate_and_plan(
    tmp_path: Path, example: str, finding: str, needle: str
) -> None:
    repo = copy_example(example, tmp_path)
    before = snapshot(repo)
    report = run_fix(
        load_finding(finding), FixOptions(repo=repo, dry_run=True, require_plan=True), runner_factory=SafeRunner
    )

    assert report.validate.ok and "valid" in report.validate.output.lower()
    assert report.plan is not None and report.plan.ok and "Plan:" in report.plan.output
    assert needle in report.diff
    assert snapshot(repo) == before  # dry-run leaves the real repo untouched (no .terraform, no lock file)


def test_venom_doctor_oom_finding_passes_real_terraform_validate_and_plan(tmp_path: Path) -> None:
    import json

    from pr_agent.workflow import FixOptions, run_fix

    from .conftest import EXAMPLES

    docs = json.loads((EXAMPLES / "findings" / "venom-doctor-output.json").read_text(encoding="utf-8"))
    finding = next(Finding.from_dict(d) for d in docs if d["id"] == "VD-K8S-002")
    repo = copy_example("k8s-oom-demo", tmp_path)
    before = snapshot(repo)
    report = run_fix(finding, FixOptions(repo=repo, dry_run=True, require_plan=True), runner_factory=SafeRunner)
    assert report.validate.ok and report.plan is not None and report.plan.ok
    assert 'memory = "64Mi"' in report.diff and snapshot(repo) == before
