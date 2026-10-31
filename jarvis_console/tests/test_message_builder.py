from __future__ import annotations

import pytest

from jarvis_console.core.errors import ReasoningLeakError
from jarvis_console.core.message_builder import MessageBuilder
from jarvis_console.core.types import LLMResponse, ToolCall, ToolResult


def test_three_round_tool_calls_no_reasoning() -> None:
    builder = MessageBuilder()
    builder.add_system("system")
    builder.add_user("user")
    for index in range(3):
        response = LLMResponse(
            content=f"round {index}",
            tool_calls=[ToolCall(f"call_{index}", "demo", {"n": index})],
            reasoning=f"private {index}",
            had_reasoning=True,
            finish_reason="tool_calls",
        )
        builder.add_assistant(response)
        builder.add_tool_results([
            ToolResult(f"call_{index}", "demo", True, text="ok")
        ])
        payload = builder.to_api_payload()
        assert "reasoning_content" not in repr(payload)


def test_reasoning_preserved_for_ui_but_not_payload() -> None:
    raw = {
        "role": "assistant",
        "content": "answer",
        "reasoning_content": "private chain",
        "tool_calls": [],
    }
    response = LLMResponse(
        content=raw["content"],
        reasoning=raw["reasoning_content"],
        had_reasoning=True,
    )
    builder = MessageBuilder()
    builder.add_assistant(response)
    assert response.reasoning == "private chain"
    assert response.had_reasoning is True
    assert "reasoning_content" not in builder.to_api_payload()[0]


def test_reasoning_leak_raises() -> None:
    builder = MessageBuilder([{"role": "assistant", "content": "x", "reasoning_content": "leak"}])
    with pytest.raises(ReasoningLeakError):
        builder.to_api_payload()
