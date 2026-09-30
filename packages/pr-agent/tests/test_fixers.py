from __future__ import annotations

from pathlib import Path

import pytest

from pr_agent import hcl
from pr_agent.fixes import AlreadyFixedError, FixerError, get_fixer
from pr_agent.fixes.ebs_gp3 import EbsGp3Fixer
from pr_agent.fixes.required_tags import RequiredTagsFixer, parse_required_tags
from pr_agent.fixes.s3_encryption import S3EncryptionFixer
from pr_agent.safety import SafeRunner
from pr_agent.tools import ToolBox

from .conftest import EXAMPLES, copy_example, load_finding, make_finding


def toolbox_for(name: str, tmp_path: Path) -> ToolBox:
    repo = copy_example(name, tmp_path)
    return ToolBox(repo, SafeRunner(repo))


def attrs(tools: ToolBox, path: str, rtype: str, rname: str) -> dict[str, object]:
    found = hcl.resource_attrs(hcl.parse(tools.read_file(path)), rtype, rname)
    assert found is not None
    return found


def test_registry_maps_ids_to_fixers() -> None:
    assert isinstance(get_fixer("TF-S3-001"), S3EncryptionFixer)
    assert isinstance(get_fixer("TF-TAG-001"), RequiredTagsFixer)
    assert isinstance(get_fixer("TF-EBS-001"), EbsGp3Fixer)
    assert get_fixer("KD-K8S-001") is None


# ---- S3 encryption ---------------------------------------------------------------------------


def test_s3_fixer_adds_the_encryption_configuration(toolbox: ToolBox) -> None:
    outcome = S3EncryptionFixer().apply(load_finding("s3-no-encryption.json"), toolbox)
    enc = attrs(toolbox, "main.tf", "aws_s3_bucket_server_side_encryption_configuration", "demo_logs")
    assert "aws_s3_bucket.demo_logs.id" in str(enc["bucket"])
    assert "AES256" in str(enc["rule"])
    assert outcome.files == {"main.tf"} and outcome.risk == "low"
    # the original resources are untouched
    assert attrs(toolbox, "main.tf", "aws_s3_bucket", "demo_logs")["bucket"] == "example-demo-logs-bucket"


def test_s3_fixer_is_idempotent(toolbox: ToolBox) -> None:
    finding = load_finding("s3-no-encryption.json")
    S3EncryptionFixer().apply(finding, toolbox)
    before = toolbox.read_file("main.tf")
    with pytest.raises(AlreadyFixedError):
        S3EncryptionFixer().apply(finding, toolbox)
    assert toolbox.read_file("main.tf") == before


def test_s3_fixer_detects_encryption_defined_in_another_file(toolbox: ToolBox, s3_repo: Path) -> None:
    (s3_repo / "enc.tf").write_text(
        'resource "aws_s3_bucket_server_side_encryption_configuration" "other_label" {\n'
        "  bucket = aws_s3_bucket.demo_logs.id\n"
        '  rule {\n    apply_server_side_encryption_by_default {\n      sse_algorithm = "aws:kms"\n    }\n  }\n}\n'
    )
    with pytest.raises(AlreadyFixedError):
        S3EncryptionFixer().apply(load_finding("s3-no-encryption.json"), toolbox)


def test_s3_fixer_fails_clearly_when_the_bucket_does_not_exist(toolbox: ToolBox) -> None:
    finding = make_finding(resource={"type": "aws_s3_bucket", "name": "ghost", "path": "main.tf"})
    with pytest.raises(FixerError, match="ghost"):
        S3EncryptionFixer().apply(finding, toolbox)


def test_s3_fixer_rejects_other_resource_types(toolbox: ToolBox) -> None:
    finding = make_finding(resource={"type": "aws_ebs_volume", "name": "demo_logs", "path": "main.tf"})
    with pytest.raises(FixerError, match="not supported"):
        S3EncryptionFixer().apply(finding, toolbox)


def test_s3_fixer_finds_the_bucket_without_a_path_hint(toolbox: ToolBox) -> None:
    finding = make_finding(resource={"type": "aws_s3_bucket", "name": "demo_logs"})
    assert S3EncryptionFixer().apply(finding, toolbox).files == {"main.tf"}


# ---- required tags ---------------------------------------------------------------------------


def test_tags_fixer_adds_only_the_missing_tags(tmp_path: Path) -> None:
    tools = toolbox_for("tags-demo", tmp_path)
    outcome = RequiredTagsFixer().apply(load_finding("missing-tags.json"), tools)
    tags = attrs(tools, "main.tf", "aws_s3_bucket", "reports")["tags"]
    assert tags == {"Name": "reports", "Environment": "dev", "Owner": "platform-team"}
    assert outcome.files == {"main.tf"}
    # the other resource in the file was not touched
    assert "tags" not in attrs(tools, "main.tf", "aws_ebs_volume", "scratch")


def test_tags_fixer_never_overwrites_an_existing_value(tmp_path: Path) -> None:
    tools = toolbox_for("tags-demo", tmp_path)
    finding = make_finding(
        id="TF-TAG-001",
        resource={"type": "aws_s3_bucket", "name": "reports", "path": "main.tf"},
        evidence=[{"kind": "required-tags", "detail": "Name=OVERRIDE;Environment=dev"}],
    )
    RequiredTagsFixer().apply(finding, tools)
    tags = attrs(tools, "main.tf", "aws_s3_bucket", "reports")["tags"]
    assert isinstance(tags, dict) and tags["Name"] == "reports" and tags["Environment"] == "dev"


def test_tags_fixer_is_idempotent(tmp_path: Path) -> None:
    tools = toolbox_for("tags-demo", tmp_path)
    finding = load_finding("missing-tags.json")
    RequiredTagsFixer().apply(finding, tools)
    with pytest.raises(AlreadyFixedError):
        RequiredTagsFixer().apply(finding, tools)


def test_tags_fixer_needs_the_values_in_the_finding(tmp_path: Path) -> None:
    tools = toolbox_for("tags-demo", tmp_path)
    finding = make_finding(
        id="TF-TAG-001",
        resource={"type": "aws_s3_bucket", "name": "reports", "path": "main.tf"},
        evidence=[{"kind": "note", "detail": "tags missing"}],
    )
    with pytest.raises(FixerError, match="required-tags"):
        RequiredTagsFixer().apply(finding, tools)


def test_parse_required_tags_handles_spaces_and_equals_in_values() -> None:
    finding = make_finding(evidence=[{"kind": "required-tags", "detail": " A = 1 ; B=x=y ;broken; =novalue;C="}])
    assert parse_required_tags(finding) == {"A": "1", "B": "x=y"}


# ---- gp2 -> gp3 ------------------------------------------------------------------------------


def test_ebs_fixer_changes_only_the_named_volume(tmp_path: Path) -> None:
    tools = toolbox_for("ebs-demo", tmp_path)
    outcome = EbsGp3Fixer().apply(load_finding("ebs-gp2.json"), tools)
    assert attrs(tools, "main.tf", "aws_ebs_volume", "data")["type"] == "gp3"
    assert attrs(tools, "main.tf", "aws_ebs_volume", "archive")["type"] == "gp2"  # a different resource
    assert outcome.risk == "low"


def test_ebs_fixer_flags_large_volumes_as_medium_risk(tmp_path: Path) -> None:
    tools = toolbox_for("ebs-demo", tmp_path)
    finding = make_finding(id="TF-EBS-001", resource={"type": "aws_ebs_volume", "name": "archive", "path": "main.tf"})
    outcome = EbsGp3Fixer().apply(finding, tools)
    assert outcome.risk == "medium" and any("2000 GiB" in d for d in outcome.details)


def test_ebs_fixer_handles_instance_block_devices_and_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "inst"
    repo.mkdir()
    (repo / "main.tf").write_text(
        'resource "aws_instance" "web" {\n  ami = "ami-0123456789abcdef0"\n  instance_type = "t3.micro"\n\n'
        '  root_block_device {\n    volume_type = "gp2"\n  }\n}\n'
    )
    tools = ToolBox(repo, SafeRunner(repo))
    finding = make_finding(id="TF-EBS-001", resource={"type": "aws_instance", "name": "web", "path": "main.tf"})
    EbsGp3Fixer().apply(finding, tools)
    assert 'volume_type = "gp3"' in tools.read_file("main.tf")
    with pytest.raises(AlreadyFixedError):
        EbsGp3Fixer().apply(finding, tools)


def test_examples_are_deliberately_broken() -> None:
    assert "server_side_encryption" not in (EXAMPLES / "terraform/s3-demo/main.tf").read_text()
    assert 'type              = "gp2"' in (EXAMPLES / "terraform/ebs-demo/main.tf").read_text()
