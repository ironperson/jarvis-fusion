from __future__ import annotations

from pathlib import Path
from typing import Any

from jarvis_console.core.agent_loop import AgentLoop
from jarvis_console.core.llm_client import EngineRouter, LLMClient, MockLLMClient
from jarvis_console.core.memory import MemoryStore
from jarvis_console.core.message_builder import MessageBuilder
from jarvis_console.core.safety import ConfirmationGate, PathPolicy, SafetyGate
from jarvis_console.core.tool_registry import REGISTRY, ToolRegistry
from jarvis_console.core.types import TurnContext


def _assert_payload_clean(payload: list[dict[str, Any]]) -> None:
    """递归断言出站载荷不含 reasoning_content 键（文本子串不参与判定）。"""
    MessageBuilder.assert_no_reasoning(payload)
    for message in payload:
        for key in message:
            assert key != "reasoning_content"
        for call in message.get("tool_calls") or []:
            assert "reasoning_content" not in call


def test_mock_full_chain_and_reasoning_never_leaks(tmp_db: Path, tmp_path: Path) -> None:
    import jarvis_console.tools  # noqa: F401

    client = MockLLMClient()
    loop = AgentLoop(
        REGISTRY,
        EngineRouter(client),
        MemoryStore(tmp_path / "memory.db", ollama_base_url="http://127.0.0.1:1"),
        max_rounds=8,
    )
    context = TurnContext("帮我搜集抖音播放量最高的 AI 视频")
    outcome = loop.run(context.user_text, context)
    assert outcome.error == ""
    assert outcome.rounds == 4
    assert outcome.ingested_note_ids
    assert "抖音站内搜索本版没开" in outcome.answer
    assert any(item["name"] == "search_bilibili" for item in outcome.trace)
    assert any(item["name"] == "write_file" for item in outcome.trace)
    assert len(client.received_messages) == 4
    for messages in client.received_messages:
        _assert_payload_clean(messages)
    _assert_payload_clean(loop.builder.to_api_payload())


def _destructive_registry(counter: dict[str, int]) -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register("delete_file", "delete", "file", destructive=True)
    def delete_file(path: str) -> str:
        counter["calls"] += 1
        return f"deleted {path}"

    return registry


def _destructive_script(path: Path, final: str) -> list[dict[str, Any]]:
    return [
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "d1",
                    "type": "function",
                    "function": {"name": "delete_file", "arguments": {"path": str(path)}},
                }],
            },
            "finish_reason": "tool_calls",
        },
        {"message": {"role": "assistant", "content": final}, "finish_reason": "stop"},
    ]


def test_confirmation_cancel_then_model_continues(tmp_path: Path) -> None:
    target = tmp_path / "allowed" / "x.txt"
    counter = {"calls": 0}
    registry = _destructive_registry(counter)
    holder: dict[str, ConfirmationGate] = {}

    def reject(payload: dict) -> None:
        holder["gate"].resolve(False)

    confirmation = ConfirmationGate(reject)
    holder["gate"] = confirmation
    safety = SafetyGate(
        PathPolicy(allowed_roots=[target.parent], sensitive_dirs=[]),
        confirmation,
        confirmation_timeout=0.2,
    )
    loop = AgentLoop(
        registry,
        EngineRouter(MockLLMClient(script=_destructive_script(target, "好的，我没动它。"))),
        MemoryStore(tmp_path / "memory.db", ollama_base_url="http://127.0.0.1:1"),
    )
    outcome = loop.run("删除文件", TurnContext("删除文件", gate=safety))
    assert counter["calls"] == 0
    assert outcome.trace[0]["cancelled"] is True
    assert outcome.answer == "好的，我没动它。"


def test_confirmation_approve_executes(tmp_path: Path) -> None:
    target = tmp_path / "allowed" / "x.txt"
    counter = {"calls": 0}
    registry = _destructive_registry(counter)
    holder: dict[str, ConfirmationGate] = {}

    def approve(payload: dict) -> None:
        holder["gate"].resolve(True)

    confirmation = ConfirmationGate(approve)
    holder["gate"] = confirmation
    safety = SafetyGate(
        PathPolicy(allowed_roots=[target.parent], sensitive_dirs=[]),
        confirmation,
        confirmation_timeout=0.2,
    )
    loop = AgentLoop(
        registry,
        EngineRouter(MockLLMClient(script=_destructive_script(target, "已按确认执行。"))),
        MemoryStore(tmp_path / "memory2.db", ollama_base_url="http://127.0.0.1:1"),
    )
    outcome = loop.run("删除文件", TurnContext("删除文件", gate=safety))
    assert counter["calls"] == 1
    assert outcome.trace[0]["ok"] is True


def test_round_limit_is_reported(tmp_path: Path) -> None:
    registry = ToolRegistry()

    @registry.register("ping", "ping", "test")
    def ping() -> str:
        return "pong"

    script = []
    for index in range(2):
        script.append({
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": f"p{index}",
                    "type": "function",
                    "function": {"name": "ping", "arguments": {}},
                }],
            },
            "finish_reason": "tool_calls",
        })
    loop = AgentLoop(
        registry,
        EngineRouter(MockLLMClient(script=script)),
        MemoryStore(tmp_path / "memory3.db", ollama_base_url="http://127.0.0.1:1"),
        max_rounds=2,
    )
    outcome = loop.run("一直调用", TurnContext("一直调用"))
    assert outcome.reached_limit is True
    assert "已达上限 2 轮" in outcome.answer
