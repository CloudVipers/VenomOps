from __future__ import annotations

import pytest

from pr_agent import hcl

TF = """# resource "aws_s3_bucket" "ghost" { commented out }
resource "aws_s3_bucket" "logs" {
  bucket = "demo-logs" # trailing { brace in a comment
  tags = {
    Name = "logs"
  }
}

resource "aws_ebs_volume" "data" {
  availability_zone = "us-east-1a"
  type              = "gp2"
  size              = 10
  description       = "interp ${var.x != "" ? "{y}" : "z"} and type = \\"gp2\\""
}

resource "aws_instance" "web" {
  ami           = "ami-0123456789abcdef0"
  instance_type = "t3.micro"

  root_block_device {
    volume_type = "gp2"
  }
  ebs_block_device {
    device_name = "/dev/sdb"
    volume_type = "gp2"
  }
}
"""


def test_sanitize_blanks_strings_comments_and_heredocs_keeping_length() -> None:
    src = 'a = "x{" # c }\nb = <<EOT\n{ }\nEOT\nc {\n}\n'
    clean = hcl.sanitize(src)
    assert len(clean) == len(src) and clean.count("\n") == src.count("\n")
    assert clean.count("{") == 1 and clean.count("}") == 1  # only the real block braces survive


def test_find_resource_ignores_commented_and_returns_exact_block() -> None:
    assert hcl.find_resource(TF, "aws_s3_bucket", "ghost") is None
    block = hcl.find_resource(TF, "aws_s3_bucket", "logs")
    assert block is not None
    body = TF[block.start : block.end]
    assert body.startswith('resource "aws_s3_bucket" "logs"') and body.rstrip().endswith("}")
    assert "aws_ebs_volume" not in body
    assert hcl.has_resource(TF, "aws_ebs_volume", "data") and not hcl.has_resource(TF, "aws_ebs_volume", "nope")


def test_braces_inside_interpolation_do_not_break_block_matching() -> None:
    block = hcl.find_resource(TF, "aws_ebs_volume", "data")
    assert block is not None
    assert "size              = 10" in TF[block.start : block.end]


def test_replace_attr_value_changes_only_real_code() -> None:
    block = hcl.find_resource(TF, "aws_ebs_volume", "data")
    assert block is not None
    new, count = hcl.replace_attr_value(TF, block, "type", "gp2", "gp3")
    assert count == 1
    assert 'type              = "gp3"' in new
    assert 'type = \\"gp2\\"' in new  # the text inside the description string is untouched
    hcl.parse(new)


def test_replace_attr_value_reaches_nested_blocks_but_not_other_resources() -> None:
    block = hcl.find_resource(TF, "aws_instance", "web")
    assert block is not None
    new, count = hcl.replace_attr_value(TF, block, "volume_type", "gp2", "gp3")
    assert count == 2 and new.count('volume_type = "gp3"') == 2
    assert 'type              = "gp2"' in new  # the EBS volume resource is a different block
    assert hcl.replace_attr_value(TF, block, "volume_type", "io1", "gp3")[1] == 0


def test_replace_attr_value_does_not_match_a_longer_attribute_name() -> None:
    src = 'resource "aws_ebs_volume" "v" {\n  snapshot_type = "gp2"\n  type = "standard"\n}\n'
    block = hcl.find_resource(src, "aws_ebs_volume", "v")
    assert block is not None
    assert hcl.replace_attr_value(src, block, "type", "gp2", "gp3")[1] == 0


def test_ensure_tags_adds_only_missing_keys_and_keeps_existing_values() -> None:
    block = hcl.find_resource(TF, "aws_s3_bucket", "logs")
    assert block is not None
    new, added = hcl.ensure_tags(TF, block, {"Name": "IGNORED", "Environment": "prod", "Owner": "team-a"})
    assert added == ["Environment", "Owner"]
    assert 'Name = "logs"' in new and "IGNORED" not in new
    assert 'Environment = "prod"' in new and 'Owner = "team-a"' in new
    attrs = hcl.resource_attrs(hcl.parse(new), "aws_s3_bucket", "logs")
    assert attrs is not None
    assert attrs["tags"] == {"Name": "logs", "Environment": "prod", "Owner": "team-a"}


def test_ensure_tags_is_a_noop_when_everything_is_present() -> None:
    block = hcl.find_resource(TF, "aws_s3_bucket", "logs")
    assert block is not None
    new, added = hcl.ensure_tags(TF, block, {"Name": "x"})
    assert added == [] and new == TF


def test_ensure_tags_creates_the_attribute_when_missing() -> None:
    block = hcl.find_resource(TF, "aws_ebs_volume", "data")
    assert block is not None
    new, added = hcl.ensure_tags(TF, block, {"Environment": "prod"})
    assert added == ["Environment"]
    vol = hcl.resource_attrs(hcl.parse(new), "aws_ebs_volume", "data")
    assert vol is not None
    assert vol["tags"] == {"Environment": "prod"} and vol["size"] == 10


def test_ensure_tags_ignores_tags_of_nested_blocks() -> None:
    src = 'resource "aws_instance" "w" {\n  ami = "a"\n  root_block_device {\n    tags = { K = "v" }\n  }\n}\n'
    block = hcl.find_resource(src, "aws_instance", "w")
    assert block is not None
    new, added = hcl.ensure_tags(src, block, {"Environment": "prod"})
    assert added == ["Environment"]
    inst = hcl.resource_attrs(hcl.parse(new), "aws_instance", "w")
    assert inst is not None
    assert inst["tags"] == {"Environment": "prod"}  # new top-level tags, nested ones untouched


@pytest.mark.parametrize(
    "tags_line",
    ["tags = var.common_tags", 'tags = merge(var.common_tags, { A = "b" })', 'tags = { A = "b" }'],
)
def test_ensure_tags_refuses_what_it_cannot_edit_safely(tags_line: str) -> None:
    src = f'resource "aws_s3_bucket" "b" {{\n  bucket = "x"\n  {tags_line}\n}}\n'
    block = hcl.find_resource(src, "aws_s3_bucket", "b")
    assert block is not None
    with pytest.raises(hcl.EditError):
        hcl.ensure_tags(src, block, {"Environment": "prod"})


def test_append_block_and_parse_validation() -> None:
    out = hcl.append_block(TF, 'resource "aws_s3_bucket_versioning" "v" {\n  bucket = "b"\n}\n')
    assert out.endswith("}\n") and '\n\nresource "aws_s3_bucket_versioning"' in out
    hcl.parse(out)
    with pytest.raises(hcl.EditError):
        hcl.parse('resource "x" "y" {')


def test_tag_values_are_escaped() -> None:
    block = hcl.find_resource(TF, "aws_ebs_volume", "data")
    assert block is not None
    new, _ = hcl.ensure_tags(TF, block, {"Note": 'say "hi" ${not_interpolated}'})
    # quotes are escaped and "${" becomes "$${" so the value can never be interpolated or break the file
    assert 'Note = "say \\"hi\\" $${not_interpolated}"' in new
    assert hcl.resource_attrs(hcl.parse(new), "aws_ebs_volume", "data") is not None
