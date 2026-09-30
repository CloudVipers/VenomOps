"""The only place that talks to a model: Amazon Bedrock Converse with a *forced* output tool.

The committee never asks for free text: each call declares one tool whose input schema is the structure we want,
and ``toolChoice`` forces the model to call it, so the answer is JSON we can validate. There is deliberately no
default model (rule 5 in CLAUDE.md): callers must pass one explicitly.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(RuntimeError):
    """The model call failed or did not return the requested structure."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class LLM(Protocol):
    """One structured call. Implementations: :class:`BedrockLLM` (real) and scripted fakes in the tests."""

    def converse_tool(
        self, *, system: str, user: str, tool_name: str, tool_description: str, schema: dict[str, Any], max_tokens: int
    ) -> tuple[dict[str, Any], Usage]: ...


class BedrockLLM:
    def __init__(self, client: Any, model_id: str) -> None:
        if not model_id.strip():
            raise ValueError("an explicit Bedrock model id is required (there is no default)")
        self._client = client
        self.model_id = model_id

    def converse_tool(
        self, *, system: str, user: str, tool_name: str, tool_description: str, schema: dict[str, Any], max_tokens: int
    ) -> tuple[dict[str, Any], Usage]:
        try:
            response = self._client.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                toolConfig={
                    "tools": [
                        {
                            "toolSpec": {
                                "name": tool_name,
                                "description": tool_description,
                                "inputSchema": {"json": schema},
                            }
                        }
                    ],
                    "toolChoice": {"tool": {"name": tool_name}},
                },
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
            )
        except Exception as exc:  # noqa: BLE001 - botocore raises many types; surface one clear error
            raise LLMError(f"Bedrock call failed: {exc}") from exc

        usage = response.get("usage", {})
        used = Usage(int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0)))
        if response.get("stopReason") == "max_tokens":
            # Bedrock returns the tool input cut off mid-way; fields that come later are simply missing, which models
            # with defaults would accept silently (found in a real run). Never use a truncated answer.
            raise LLMError(f"the answer was cut off at max_tokens={max_tokens}; raise --max-output-tokens")
        for block in response.get("output", {}).get("message", {}).get("content", []):
            call = block.get("toolUse")
            if call and call.get("name") == tool_name:
                payload = call.get("input")
                if isinstance(payload, dict):
                    return payload, used
        raise LLMError(
            f"the model did not call the required tool {tool_name!r} (stopReason={response.get('stopReason')})"
        )


def make_bedrock_llm(model_id: str, region: str | None = None) -> BedrockLLM:
    """Build a Bedrock-backed LLM; fails clearly if AWS credentials are not available."""
    import boto3

    session = boto3.Session(region_name=region)
    if session.get_credentials() is None:
        raise RuntimeError("no AWS credentials available (configure AWS_PROFILE or environment variables)")
    return BedrockLLM(session.client("bedrock-runtime"), model_id)


class Budget:
    """Global token budget shared by every call (thread-safe). Calls reserve an estimate first, then settle."""

    def __init__(self, max_total_tokens: int, max_rounds: int) -> None:
        self.max_total_tokens = max_total_tokens
        self.max_rounds = max_rounds
        self.used = 0
        self._reserved = 0
        self._lock = threading.Lock()

    @property
    def remaining(self) -> int:
        return max(0, self.max_total_tokens - self.used - self._reserved)

    def reserve(self, estimate: int) -> bool:
        with self._lock:
            if self.used + self._reserved + estimate > self.max_total_tokens:
                return False
            self._reserved += estimate
            return True

    def settle(self, reserved: int, actual: int) -> None:
        with self._lock:
            self._reserved -= reserved
            self.used += actual

    def release(self, reserved: int) -> None:
        with self._lock:
            self._reserved -= reserved
