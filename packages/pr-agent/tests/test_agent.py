from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from pr_agent.agent import SYSTEM_PROMPT, build_agent, dispatch_tool
from pr_agent.tools import ToolBox, ToolError
from pr_agent.workflow import FixAborted, FixOptions, run_fix

from .conftest import make_finding

SNIPPET = (
    'resource "aws_s3_bucket_versioning" "extra" {\n  bucket = aws_s3_bucket.demo_logs.id\n'
    '  versioning_configuration {\n    status = "Enabled"\n  }\n}'
)


def tool_use(name: str, args: dict[str, Any], uid: str = "t1") -> dict[str, Any]:
    return {
        "output": {
            "message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": uid, "name": name, "input": args}}]}
        },
        "stopReason": "tool_use",
    }


def final(text: str) -> dict[str, Any]:
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}, "stopReason": "end_turn"}


class ScriptedBedrock:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(copy.deepcopy(kwargs))  # the agent keeps mutating its message list
        return self.responses.pop(0)


def test_agent_reads_edits_and_reports(toolbox: ToolBox) -> None:
    bedrock = ScriptedBedrock(
        [
            tool_use("list_files", {}),
            tool_use("read_file", {"path": "main.tf"}, "t2"),
            tool_use("edit_hcl", {"path": "main.tf", "patch": {"op": "append_block", "content": SNIPPET}}, "t3"),
            final("Enable versioning on the logs bucket.\nDone."),
        ]
    )
    outcome = build_agent(bedrock, "test-model")(make_finding(id="CUSTOM-001"), toolbox)
    assert outcome.summary == "Enable versioning on the logs bucket" and outcome.files == {"main.tf"}
    assert "aws_s3_bucket_versioning" in toolbox.read_file("main.tf")

    first = bedrock.requests[0]
    assert first["modelId"] == "test-model" and first["system"][0]["text"] == SYSTEM_PROMPT
    assert [t["toolSpec"]["name"] for t in first["toolConfig"]["tools"]] == [
        "read_file", "list_files", "edit_hcl", "terraform_validate", "terraform_plan",
    ]  # fmt: skip
    # tool results were fed back to the model
    last_user = bedrock.requests[-1]["messages"][-1]
    assert last_user["role"] == "user" and last_user["content"][0]["toolResult"]["content"][0]["text"] == "edited"


def test_the_finding_is_redacted_before_it_reaches_the_model(toolbox: ToolBox) -> None:
    finding = make_finding(
        id="CUSTOM-001",
        evidence=[{"kind": "log", "detail": "password=hunter2 in account 123456789012"}],
    )
    bedrock = ScriptedBedrock([final("nothing")])
    with pytest.raises(FixAborted, match="without changing"):
        build_agent(bedrock, "m")(finding, toolbox)
    sent = bedrock.requests[0]["messages"][0]["content"][0]["text"]
    assert "hunter2" not in sent and "123456789012" not in sent


def test_a_misbehaving_model_cannot_escape_the_boundaries(toolbox: ToolBox, s3_repo: Path) -> None:
    (s3_repo / "terraform.tfstate").write_text("{}")
    bedrock = ScriptedBedrock(
        [
            tool_use("read_file", {"path": "../../etc/passwd"}, "a"),
            tool_use("read_file", {"path": "terraform.tfstate"}, "b"),
            tool_use("run_shell", {"cmd": "terraform apply -auto-approve"}, "c"),
            tool_use("terraform_apply", {}, "d"),
            tool_use("edit_hcl", {"path": "main.tf", "patch": {"op": "append_block", "content": "resource {"}}, "e"),
            final("gave up"),
        ]
    )
    with pytest.raises(FixAborted, match="without changing"):
        build_agent(bedrock, "m")(make_finding(id="CUSTOM-001"), toolbox)
    statuses = [req["messages"][-1]["content"][0]["toolResult"]["status"] for req in bedrock.requests[1:]]
    assert statuses == ["error"] * 5  # every attempt was refused and reported back, none executed
    assert "{}" not in (s3_repo / "main.tf").read_text()


def test_turn_limit(toolbox: ToolBox) -> None:
    looping = ScriptedBedrock([tool_use("list_files", {}, f"t{i}") for i in range(5)])
    with pytest.raises(FixAborted, match="did not finish within 3 turns"):
        build_agent(looping, "m", max_turns=3)(make_finding(id="CUSTOM-001"), toolbox)


def test_an_explicit_model_is_mandatory() -> None:
    with pytest.raises(ValueError, match="explicit Bedrock model"):
        build_agent(ScriptedBedrock([]), "  ")


def test_dispatch_rejects_unknown_tools_and_bad_arguments(toolbox: ToolBox) -> None:
    with pytest.raises(ToolError, match="unknown tool"):
        dispatch_tool(toolbox, "terraform_apply", {})
    with pytest.raises(ToolError, match="patch"):
        dispatch_tool(toolbox, "edit_hcl", {"path": "main.tf", "patch": "not-an-object"})
    assert "main.tf" in dispatch_tool(toolbox, "list_files", {})


def test_agent_output_still_goes_through_the_workflow_checks(
    s3_repo: Path, runner_factory: Any, tf_calls: list[list[str]]
) -> None:
    bedrock = ScriptedBedrock(
        [
            tool_use("edit_hcl", {"path": "main.tf", "patch": {"op": "append_block", "content": SNIPPET}}),
            tool_use("terraform_validate", {}, "v"),
            final("Enable versioning."),
        ]
    )
    report = run_fix(
        make_finding(id="CUSTOM-001"),
        FixOptions(repo=s3_repo, dry_run=True),
        runner_factory=runner_factory,
        agent=build_agent(bedrock, "m"),
    )
    assert "aws_s3_bucket_versioning" in report.diff and "generado por el agente" in report.body.lower()
    assert [c[1] for c in tf_calls].count("validate") == 2  # the model's own + the workflow's final check
