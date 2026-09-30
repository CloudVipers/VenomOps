"""OPTIONAL LLM agent (Amazon Bedrock Converse with tool use) for findings that have no deterministic fixer.

It is never used by default: the CLI needs ``--agent`` *and* an explicit model id, and the finding is
redacted before it is sent. The model can only act through :class:`~pr_agent.tools.ToolBox`, so the
hard boundaries (command allowlist, path confinement, verified structured edits) apply no matter what the
model asks for. After the run, the workflow still enforces "minimal diff" and ``terraform validate``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from findings_schema import Finding

from .fixes import FixOutcome
from .redact import redact
from .safety import SecurityError
from .tools import TOOL_SPECS, ToolBox, ToolError
from .workflow import FixAborted

MAX_TOOL_OUTPUT = 6000

SYSTEM_PROMPT = """You are a careful Terraform engineer. You receive ONE finding about a Terraform repository \
(secrets already masked as [REDACTED]) and must fix it with the smallest possible change.

Rules:
- Use only the provided tools. Read the relevant files first (list_files, read_file).
- Edit only with edit_hcl structured operations. Change only what the finding requires: no refactors, \
no renames, no unrelated files, no formatting changes.
- After editing, call terraform_validate and fix any error it reports.
- Never ask for, invent or write credentials, tokens or account ids.
- When done, reply with a short plain-text summary of the change (no code blocks)."""


class BedrockConverse(Protocol):
    """The part of the boto3 ``bedrock-runtime`` client that we use (mockable in tests)."""

    def converse(self, **kwargs: Any) -> dict[str, Any]: ...


def make_bedrock_client(region: str | None = None) -> BedrockConverse:
    """Create a Bedrock runtime client; fails clearly when no AWS credentials are available."""
    import boto3

    session = boto3.Session(region_name=region)
    if session.get_credentials() is None:
        raise RuntimeError("no AWS credentials available for --agent (configure AWS_PROFILE or environment variables)")
    client: BedrockConverse = session.client("bedrock-runtime")
    return client


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_TOOL_OUTPUT else text[:MAX_TOOL_OUTPUT] + "\n… (truncated)"


def dispatch_tool(tools: ToolBox, name: str, args: dict[str, Any]) -> str:
    """Run one tool call. Raises ToolError/SecurityError for anything refused; never executes unknown tools."""
    if name == "read_file":
        return tools.read_file(str(args.get("path", "")))
    if name == "list_files":
        return "\n".join(tools.list_files(str(args.get("glob") or "**/*.tf")))
    if name == "edit_hcl":
        patch = args.get("patch")
        if not isinstance(patch, dict):
            raise ToolError("edit_hcl requires a 'patch' object")
        changed = tools.edit_hcl(str(args.get("path", "")), patch)
        return "edited" if changed else "no change"
    if name == "terraform_validate":
        return redact(tools.terraform_validate().output)
    if name == "terraform_plan":
        return redact(tools.terraform_plan().output)
    raise ToolError(f"unknown tool {name!r}")


def build_agent(
    client: BedrockConverse, model_id: str, *, max_turns: int = 12, max_tokens: int = 2048
) -> Callable[[Finding, ToolBox], FixOutcome]:
    """Return an agent callable compatible with :func:`pr_agent.workflow.run_fix`."""
    if not model_id.strip():
        raise ValueError("an explicit Bedrock model id is required for --agent (there is no default)")

    def run(finding: Finding, tools: ToolBox) -> FixOutcome:
        payload = redact(json.dumps(finding.to_dict(), ensure_ascii=False, indent=2))
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"text": f"Fix this finding with the minimal Terraform change:\n\n{payload}"}]}
        ]
        tool_config = {"tools": [{"toolSpec": spec} for spec in TOOL_SPECS]}
        final_text = ""

        for _ in range(max_turns):
            response = client.converse(
                modelId=model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                toolConfig=tool_config,
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
            )
            message = response["output"]["message"]
            messages.append(message)
            blocks = message.get("content", [])
            final_text = "\n".join(b["text"] for b in blocks if "text" in b).strip() or final_text

            if response.get("stopReason") != "tool_use":
                break

            results: list[dict[str, Any]] = []
            for block in blocks:
                call = block.get("toolUse")
                if not call:
                    continue
                try:
                    output, status = _truncate(dispatch_tool(tools, call["name"], call.get("input") or {})), "success"
                except (ToolError, SecurityError, ValueError) as exc:
                    output, status = f"refused: {exc}", "error"  # the model sees why and may adapt
                results.append(
                    {"toolResult": {"toolUseId": call["toolUseId"], "content": [{"text": output}], "status": status}}
                )
            messages.append({"role": "user", "content": results})
        else:
            raise FixAborted(f"the agent did not finish within {max_turns} turns")

        if not tools.edits:
            raise FixAborted("the agent finished without changing any file")
        files = frozenset(e.path for e in tools.edits)
        summary = (
            final_text.splitlines()[0] if final_text else f"Corrección de {finding.id} propuesta por el agente"
        ).strip()
        return FixOutcome(
            summary=summary.rstrip("."),
            details=[
                "Cambio generado por el agente LLM (opcional); revisar con especial atención.",
                *[f"{e.path}: {e.detail}" for e in tools.edits],
            ],
            risk=finding.risk_of_fix.value,
            files=files,
        )

    return run
