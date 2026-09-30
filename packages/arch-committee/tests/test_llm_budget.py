from __future__ import annotations

import threading
from typing import Any

import pytest

from arch_committee.llm import BedrockLLM, Budget, LLMError, Usage, make_bedrock_llm

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


class FakeClient:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.request: dict[str, Any] = {}

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.request = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def tool_response(name: str = "t", payload: Any = None, usage: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "1", "name": name, "input": payload or {"x": "ok"}}}],
            }
        },
        "stopReason": "tool_use",
        "usage": usage or {"inputTokens": 10, "outputTokens": 5},
    }


def call(llm: BedrockLLM) -> tuple[dict[str, Any], Usage]:
    return llm.converse_tool(
        system="sys", user="usr", tool_name="t", tool_description="d", schema=SCHEMA, max_tokens=99
    )


def test_the_output_tool_is_forced_and_usage_is_returned() -> None:
    client = FakeClient(tool_response())
    payload, usage = call(BedrockLLM(client, "some-model"))
    assert payload == {"x": "ok"} and usage == Usage(10, 5) and usage.total == 15
    req = client.request
    assert req["modelId"] == "some-model" and req["toolConfig"]["toolChoice"] == {"tool": {"name": "t"}}
    assert req["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"] == {"json": SCHEMA}
    assert req["inferenceConfig"] == {"maxTokens": 99, "temperature": 0.0}
    assert req["system"] == [{"text": "sys"}] and req["messages"][0]["content"] == [{"text": "usr"}]


def test_a_model_must_be_explicit() -> None:
    with pytest.raises(ValueError, match="explicit Bedrock model"):
        BedrockLLM(FakeClient(tool_response()), "  ")


def test_missing_tool_call_and_api_errors_become_llm_errors() -> None:
    text_only = {"output": {"message": {"content": [{"text": "I refuse"}]}}, "stopReason": "end_turn", "usage": {}}
    with pytest.raises(LLMError, match="did not call"):
        call(BedrockLLM(FakeClient(text_only), "m"))
    with pytest.raises(LLMError, match="did not call"):
        call(BedrockLLM(FakeClient(tool_response(name="other")), "m"))
    with pytest.raises(LLMError, match="throttled"):
        call(BedrockLLM(FakeClient(RuntimeError("throttled")), "m"))


def test_make_bedrock_llm_needs_credentials_and_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent")
    with pytest.raises(RuntimeError, match="credentials"):
        make_bedrock_llm("some-model", "us-east-1")


def test_budget_reserve_settle_release() -> None:
    b = Budget(1000, 2)
    assert b.reserve(600) and b.remaining == 400
    assert not b.reserve(500)  # would exceed the cap
    b.settle(600, 450)
    assert b.used == 450 and b.remaining == 550
    assert b.reserve(500) and b.remaining == 50
    b.release(500)
    assert b.remaining == 550


def test_budget_is_thread_safe() -> None:
    b = Budget(10_000, 2)
    granted: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        ok = b.reserve(100)
        with lock:
            granted.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(150)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(granted) == 100 and b.remaining == 0  # never over-commits
