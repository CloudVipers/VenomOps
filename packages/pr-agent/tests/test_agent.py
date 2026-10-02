from __future__ import annotations

import copy
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from pr_agent.agent import SYSTEM_PROMPT, build_agent, dispatch_tool
from pr_agent.safety import CommandResult
from pr_agent.tools import ToolBox, ToolError
from pr_agent.workflow import FixAborted, FixOptions, run_fix

from .conftest import FakeTerraformRunner, make_finding

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
    assert outcome.summary == "Agregar cifrado SSE-S3 al bucket" and outcome.files == {"main.tf"}  # from the finding
    assert any(
        "Enable versioning on the logs bucket" in d for d in outcome.details
    )  # the model's words, as explanation
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


def test_file_contents_are_redacted_before_they_reach_the_model(toolbox: ToolBox, s3_repo: Path) -> None:
    """Found while documenting what leaves the machine: read_file used to hand the raw Terraform to the model."""
    main = s3_repo / "main.tf"
    main.write_text(
        main.read_text()
        + '\nlocals {\n  db_password = "hunter2-very-secret"\n  deployer = "arn:aws:iam::123456789012:role/ci"\n'
        + '  key = "AKIAIOSFODNN7EXAMPLE"\n}\n'
    )
    bedrock = ScriptedBedrock([tool_use("read_file", {"path": "main.tf"}), final("nothing to change")])
    with pytest.raises(FixAborted):
        build_agent(bedrock, "m")(make_finding(id="CUSTOM-001"), toolbox)
    returned = bedrock.requests[1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"]
    for secret in ("hunter2-very-secret", "123456789012", "AKIAIOSFODNN7EXAMPLE"):
        assert secret not in returned, f"{secret!r} was sent to the model"
    assert (
        "[REDACTED]" in returned and "[ACCOUNT-ID]" in returned and "aws_s3_bucket" in returned
    )  # the code is still readable


def test_dispatch_redacts_what_read_file_returns(toolbox: ToolBox, s3_repo: Path) -> None:
    (s3_repo / "main.tf").write_text('password = "hunter2"\n')
    assert "hunter2" not in dispatch_tool(toolbox, "read_file", {"path": "main.tf"})


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


class LockFileRunner(FakeTerraformRunner):
    """Like real terraform: `init` leaves a .terraform.lock.hcl in the working directory."""

    def run(self, argv: Sequence[str]) -> CommandResult:
        result = super().run(argv)
        if argv[0] == "terraform" and argv[1] == "init":
            (self.cwd / ".terraform.lock.hcl").write_text("# provider lock\n")
        return result


def test_terraform_init_artifacts_do_not_break_the_minimal_diff_check(s3_repo: Path, tf_calls: list[list[str]]) -> None:
    """Found in a real Bedrock run: the agent calls terraform_validate, init writes the lock file, and the workflow used
    to abort with 'unexpected files created'. The lock file is not part of the fix and never reaches the real repo."""
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
        runner_factory=lambda cwd: LockFileRunner(cwd, tf_calls),
        agent=build_agent(bedrock, "m"),
    )
    assert list(report.changed_files) == ["main.tf"] and "aws_s3_bucket_versioning" in report.diff
    assert not (s3_repo / ".terraform.lock.hcl").exists()


@pytest.mark.parametrize(
    "answer",
    [
        "## Summary\n\nAdded an aws_s3_bucket_versioning resource.",
        "I've successfully fixed the finding by adding a resource. " + "It was a long paragraph. " * 40,
        "",
    ],
)
def test_the_pr_title_comes_from_the_finding_not_from_the_models_words(toolbox: ToolBox, answer: str) -> None:
    """Found in a real Bedrock run: the model answered '## Summary' / a chatty paragraph and that became the PR title."""
    bedrock = ScriptedBedrock(
        [tool_use("edit_hcl", {"path": "main.tf", "patch": {"op": "append_block", "content": SNIPPET}}), final(answer)]
    )
    outcome = build_agent(bedrock, "m")(make_finding(id="CUSTOM-001"), toolbox)
    assert outcome.summary == "Agregar cifrado SSE-S3 al bucket"
    explanation = [d for d in outcome.details if d.startswith("Explicación del agente")]
    assert all("#" not in d and len(d) < 600 for d in explanation)  # Markdown stripped, length capped


def test_an_agent_that_repeats_the_same_failing_call_is_stopped_early(toolbox: ToolBox) -> None:
    """Found in a real run: the model retried the same failing edit 8 times until the turn limit."""
    bad = {
        "path": "main.tf",
        "patch": {
            "op": "replace_attr",
            "resource": "aws_s3_bucket.demo_logs",
            "attr": "bucket",
            "old": "nope",
            "new": "x",
        },
    }
    bedrock = ScriptedBedrock([tool_use("edit_hcl", bad, f"t{i}") for i in range(8)])
    with pytest.raises(FixAborted, match="stuck repeating the same failing edit_hcl"):
        build_agent(bedrock, "m")(make_finding(id="CUSTOM-001"), toolbox)
    assert len(bedrock.requests) == 3  # stopped at the third identical failure, not after 8 turns
    hint = bedrock.requests[2]["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"]
    assert "change your approach" in hint  # the second failure already warns the model
