from __future__ import annotations

from pathlib import Path

import pytest

from pr_agent.safety import CommandNotAllowedError, PathNotAllowedError
from pr_agent.tools import TOOL_SPECS, ToolBox, ToolError


def test_read_and_list_respect_the_repository_boundary(toolbox: ToolBox, s3_repo: Path) -> None:
    (s3_repo / "terraform.tfstate").write_text('{"secret": "x"}')
    (s3_repo / "prod.tfvars").write_text('password = "hunter2"')
    assert "demo_logs" in toolbox.read_file("main.tf")
    assert toolbox.list_files() == ["main.tf", "versions.tf"]
    assert "terraform.tfstate" not in toolbox.list_files("**/*")  # secrets are invisible, not just unreadable
    assert "prod.tfvars" not in toolbox.list_files("**/*")
    for bad in ("../x.tf", "/etc/passwd", "terraform.tfstate", "prod.tfvars", ".git/config"):
        with pytest.raises(PathNotAllowedError):
            toolbox.read_file(bad)
    with pytest.raises(PathNotAllowedError):
        toolbox.list_files("../*")


def test_edit_hcl_only_edits_existing_tf_files(toolbox: ToolBox, s3_repo: Path) -> None:
    (s3_repo / "notes.md").write_text("x")
    with pytest.raises(ToolError, match="only existing"):
        toolbox.edit_hcl("notes.md", {"op": "append_block", "content": "x"})
    with pytest.raises(ToolError, match="only existing"):
        toolbox.edit_hcl("new.tf", {"op": "append_block", "content": 'resource "a" "b" {}'})
    with pytest.raises(PathNotAllowedError):
        toolbox.edit_hcl("../escape.tf", {"op": "append_block", "content": 'resource "a" "b" {}'})


def test_invalid_hcl_is_never_written(toolbox: ToolBox) -> None:
    before = toolbox.read_file("main.tf")
    with pytest.raises(ToolError, match="not valid HCL"):
        toolbox.edit_hcl("main.tf", {"op": "append_block", "content": 'resource "aws_s3_bucket" "x" {'})
    assert toolbox.read_file("main.tf") == before
    assert not list(toolbox.root.glob("*.tmp"))


@pytest.mark.parametrize(
    "patch",
    [
        {"op": "delete_everything"},
        {"op": "replace_attr", "resource": "nodots", "attr": "a", "old": "b", "new": "c"},
        {"op": "replace_attr", "resource": "aws_s3_bucket.demo_logs", "attr": "bucket", "old": "", "new": "c"},
        {"op": "replace_attr", "resource": "aws_s3_bucket.missing", "attr": "a", "old": "b", "new": "c"},
        {"op": "ensure_tags", "resource": "aws_s3_bucket.demo_logs", "tags": {}},
        {"op": "ensure_tags", "resource": "aws_s3_bucket.demo_logs", "tags": {"K": 1}},
        {"op": "append_block", "content": "   "},
    ],
)
def test_malformed_patches_are_refused(toolbox: ToolBox, patch: dict[str, object]) -> None:
    before = toolbox.read_file("main.tf")
    with pytest.raises(ToolError):
        toolbox.edit_hcl("main.tf", patch)
    assert toolbox.read_file("main.tf") == before


def test_edits_are_recorded(toolbox: ToolBox) -> None:
    assert toolbox.edit_hcl(
        "main.tf",
        {
            "op": "replace_attr",
            "resource": "aws_s3_bucket.demo_logs",
            "attr": "bucket",
            "old": "example-demo-logs-bucket",
            "new": "renamed",
        },
    )
    assert toolbox.edits and toolbox.edits[0].path == "main.tf"


def test_terraform_tools_go_through_the_allowlist(toolbox: ToolBox, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(_self: object, argv: list[str]) -> object:
        from pr_agent.safety import CommandResult, check_command

        check_command(argv)
        seen.append(list(argv))
        return CommandResult(tuple(argv), 0, "ok", "")

    monkeypatch.setattr(type(toolbox.runner), "run", fake_run)
    toolbox.terraform_validate()
    toolbox.terraform_plan()
    assert [a[1] for a in seen] == ["init", "validate", "plan"]
    assert seen[0] == ["terraform", "init", "-backend=false", "-input=false", "-no-color"]
    with pytest.raises(CommandNotAllowedError):
        toolbox.runner.run(["terraform", "apply", "-auto-approve"])


def test_tool_specs_expose_only_the_five_allowed_tools() -> None:
    assert [t["name"] for t in TOOL_SPECS] == [
        "read_file",
        "list_files",
        "edit_hcl",
        "terraform_validate",
        "terraform_plan",
    ]
