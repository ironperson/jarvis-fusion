"""QA 独立验证：reasoning_content 三层防御，针对**真实** DeepSeek / Ollama 响应结构。

工程师现有测试的缺口（本模块专门补）：
- test_llm_client.py::test_deepseek_strips_reasoning_and_disables_thinking
  只造了 {"content": "ok", "reasoning_content": "secret"}，**没有 tool_calls**。
  真实的 deepseek-reasoner / 思考模式响应是 reasoning_content 与 tool_calls
  **同时出现**，且 arguments 是 JSON 字符串而非 dict。
- test_message_builder.py::test_reasoning_preserved_for_ui_but_not_payload
  手工 new 了一个 LLMResponse 直接塞给 builder，**完全绕过了 _openai_response 解析层**，
  即"只断言自己塞进去的字段"。
本模块从 HTTP 响应体 JSON 出发，走完 DeepSeekClient.chat -> AgentLoop 全链路。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from jarvis_console.core.agent_loop import AgentLoop
from jarvis_console.core.llm_client import DeepSeekClient, EngineRouter, MockLLMClient, OllamaClient
from jarvis_console.core.memory import MemoryStore
from jarvis_console.core.message_builder import MessageBuilder
from jarvis_console.core.safety import ConfirmationGate, PathPolicy, SafetyGate
from jarvis_console.core.tool_registry import ToolRegistry
from jarvis_console.core.types import TurnContext

_MEMORY_DIR = Path(tempfile.mkdtemp(prefix="qa_reasoning_"))
_seq = iter(range(1000))


def _memory() -> MemoryStore:
    return MemoryStore(_MEMORY_DIR / f"mem{next(_seq)}.db", ollama_base_url="http://127.0.0.1:1")


class _FakeResponse:
    def __init__(self, data: dict[str, Any], ok: bool = True) -> None:
        self._data = data
        self.ok = ok

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError("http error")

    def json(self) -> dict[str, Any]:
        return self._data


class _FakeSession:
    """按脚本顺序返回 HTTP 响应体，并记录每次出站 payload。"""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = list(script)
        self.cursor = 0
        self.sent: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        del url
        self.sent.append(kwargs.get("json") or {})
        if self.cursor >= len(self.script):
            raise AssertionError("FakeSession 脚本耗尽")
        data = self.script[self.cursor]
        self.cursor += 1
        return _FakeResponse(data)

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        del url, kwargs
        return _FakeResponse({"models": []})


# 真实 deepseek-reasoner 思考模式响应：reasoning_content 与 tool_calls 同时出现，
# arguments 为 JSON 字符串，usage 带 completion_tokens_details.reasoning_tokens。
DEEPSEEK_THINKING_TURN_1: dict[str, Any] = {
    "id": "chatcmpl-0a1b2c",
    "object": "chat.completion",
    "created": 1730000000,
    "model": "deepseek-reasoner",
    "choices": [{
        "index": 0,
        "logprobs": None,
        "finish_reason": "tool_calls",
        "message": {
            "role": "assistant",
            "content": "我先查一下 B 站的 AI 视频。",
            "reasoning_content": (
                "用户要抖音数据，但抖音站内搜索本版没开。"
                "必须换成 B站 + 网页搜索，并且要如实说明来源，不能冒充抖音。"
            ),
            "tool_calls": [{
                "id": "call_0_abc",
                "type": "function",
                "function": {
                    "name": "search_bilibili",
                    "arguments": "{\"keyword\": \"AI 视频\", \"limit\": 5}",
                },
            }],
        },
    }],
    "usage": {
        "prompt_tokens": 512,
        "completion_tokens": 128,
        "total_tokens": 640,
        "completion_tokens_details": {"reasoning_tokens": 96},
    },
}

DEEPSEEK_THINKING_TURN_2: dict[str, Any] = {
    "id": "chatcmpl-0a1b2d",
    "object": "chat.completion",
    "created": 1730000001,
    "model": "deepseek-reasoner",
    "choices": [{
        "index": 0,
        "logprobs": None,
        "finish_reason": "stop",
        "message": {
            "role": "assistant",
            "content": "抖音站内搜索本版没开，我改用 B站 + 网页搜索，结果如下。",
            "reasoning_content": "把 B 站结果整理成表格，并注明来源不是抖音。",
        },
    }],
    "usage": {"prompt_tokens": 700, "completion_tokens": 200, "total_tokens": 900},
}


def _assert_no_reasoning_anywhere(value: Any, path: str = "root") -> None:
    """递归断言：任何层级都不出现 reasoning_content / thinking 键。"""
    if isinstance(value, dict):
        for key, child in value.items():
            assert key not in {"reasoning_content", "thinking"}, f"思考字段泄漏于 {path}.{key}"
            _assert_no_reasoning_anywhere(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_reasoning_anywhere(child, f"{path}[{index}]")


def test_deepseek_thinking_mode_reasoning_and_tool_calls_together() -> None:
    """真实思考模式响应：reasoning_content 与 tool_calls 同时出现时要能同时正确解析。"""
    session = _FakeSession([DEEPSEEK_THINKING_TURN_1])
    client = DeepSeekClient("test-key", session=session, disable_thinking=False)

    response = client.chat([{"role": "user", "content": "帮我找抖音播放量最高的 AI 视频"}], [])

    assert response.had_reasoning is True, "思考内容未被识别"
    assert "抖音站内搜索本版没开" in response.reasoning, "reasoning 未从真实字段提取"
    assert len(response.tool_calls) == 1, "思考模式下 tool_calls 解析失败"
    assert response.tool_calls[0].name == "search_bilibili"
    assert response.tool_calls[0].arguments == {"keyword": "AI 视频", "limit": 5}, "JSON 字符串参数未解析"
    assert response.finish_reason == "tool_calls"
    assert response.content == "我先查一下 B 站的 AI 视频。"


def test_deepseek_thinking_mode_two_turn_never_leaks_to_wire() -> None:
    """两轮真实思考模式对话：每一轮出站 payload（含 tool 消息）都不得带思考字段。"""
    session = _FakeSession([DEEPSEEK_THINKING_TURN_1, DEEPSEEK_THINKING_TURN_2])
    client = DeepSeekClient("test-key", session=session, disable_thinking=False)
    registry = ToolRegistry()

    @registry.register("search_bilibili", "B 站搜索", "search")
    def search_bilibili(keyword: str, limit: int = 5) -> dict[str, Any]:
        return {"ok": True, "text": f"B站命中 {limit} 条：{keyword}", "items": []}

    loop = AgentLoop(registry, EngineRouter(client), _memory())
    outcome = loop.run("帮我找抖音播放量最高的 AI 视频", TurnContext("找视频"))

    assert outcome.error == "", f"闭环出错：{outcome.error}"
    assert outcome.rounds == 2
    assert session.cursor == 2, "两轮脚本未全部消费"
    for index, payload in enumerate(session.sent):
        _assert_no_reasoning_anywhere(payload, f"sent[{index}]")
        _assert_no_reasoning_anywhere(payload.get("messages"), f"sent[{index}].messages")
    # 第二轮必须带上 tool 角色回灌，否则模型看不到工具结果
    roles = [m["role"] for m in session.sent[1]["messages"]]
    assert roles.count("tool") == 1, f"工具结果未回灌：{roles}"


def test_reasoning_reachable_by_ui_but_absent_from_wire() -> None:
    """reasoning 必须保留在 LLMResponse 上供 UI 展示，同时绝不出现在出站消息里。"""
    session = _FakeSession([DEEPSEEK_THINKING_TURN_1])
    client = DeepSeekClient("test-key", session=session, disable_thinking=False)
    response = client.chat([{"role": "user", "content": "x"}], [])

    builder = MessageBuilder()
    builder.add_user("x")
    builder.add_assistant(response)
    assert "reasoning_content" not in builder.messages[-1], "assistant 消息带了 reasoning_content"
    assert builder.messages[-1]["tool_calls"], "tool_calls 丢失"
    assert response.reasoning, "UI 展示用的 reasoning 丢失"
    MessageBuilder.assert_no_reasoning(builder.to_api_payload())


def test_ollama_thinking_field_is_not_leaked() -> None:
    """Ollama 思考模型的思考字段叫 thinking（不是 reasoning_content），同样不得回灌。"""
    session = _FakeSession([{
        "model": "qwen3-14b",
        "created_at": "2025-01-01T00:00:00Z",
        "done": True,
        "message": {
            "role": "assistant",
            "content": "已查到结果。",
            "thinking": "先确认用户要的是 B 站而不是抖音。",
            "tool_calls": [{"function": {"name": "ping", "arguments": {}}}],
        },
        "prompt_eval_count": 10,
        "eval_count": 5,
    }])
    client = OllamaClient(session=session)
    response = client.chat([{"role": "user", "content": "x"}], [])

    assert response.content == "已查到结果。"
    assert response.tool_calls[0].name == "ping"
    builder = MessageBuilder()
    builder.add_user("x")
    builder.add_assistant(response)
    _assert_no_reasoning_anywhere(builder.messages)


def test_leak_injected_into_tool_result_still_blocked() -> None:
    """第三层防御：若思考字段经任何途径混进 messages，出站前必须被 assert 拦下。"""
    builder = MessageBuilder([
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "y", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "p", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "name": "p",
         "content": '{"ok": true, "reasoning_content": "走私进来的思考"}'},
    ])
    # content 是字符串，不是键，因此不算泄漏（不应误报）
    payload = builder.to_api_payload()
    assert payload[2]["role"] == "tool"

    builder.messages[1]["reasoning_content"] = "真泄漏"
    from jarvis_console.core.errors import ReasoningLeakError

    with pytest.raises(ReasoningLeakError):
        builder.to_api_payload()


def test_mock_script_with_realistic_thinking_shape() -> None:
    """Mock 引擎走 choices 分支时也必须用同样的真实结构验证。"""
    mock = MockLLMClient(script=[DEEPSEEK_THINKING_TURN_1, DEEPSEEK_THINKING_TURN_2])
    registry = ToolRegistry()

    @registry.register("search_bilibili", "B 站搜索", "search")
    def search_bilibili(keyword: str, limit: int = 5) -> dict[str, Any]:
        return {"ok": True, "text": f"{keyword} {limit}"}

    loop = AgentLoop(registry, EngineRouter(mock), _memory())
    outcome = loop.run("找 AI 视频", TurnContext("找 AI 视频"))
    assert outcome.error == ""
    assert outcome.rounds == 2
    for messages in mock.received_messages:
        _assert_no_reasoning_anywhere(messages)


def test_confirmation_payload_carries_absolute_path_and_rejection_is_replayed() -> None:
    """确认门：弹窗载荷必须带完整绝对路径；用户取消后必须把"已取消"作为工具结果回灌给模型。"""
    slot = Path(tempfile.mkdtemp(prefix="qa_gate_"))
    target = slot / "allowed" / "report.md"
    target.parent.mkdir(parents=True)
    target.write_text("data", encoding="utf-8")

    seen: list[dict] = []
    holder: dict[str, ConfirmationGate] = {}

    def reject(payload: dict) -> None:
        seen.append(dict(payload))
        holder["gate"].resolve(False)

    gate = ConfirmationGate(reject)
    holder["gate"] = gate
    policy = PathPolicy(allowed_roots=[target.parent], sensitive_dirs=[])
    safety = SafetyGate(policy, gate, confirmation_timeout=0.2)

    registry = ToolRegistry()
    calls: list[dict] = []

    @registry.register("delete_file", "删除文件", "file", destructive=True)
    def delete_file(path: str) -> str:
        calls.append({"path": path})
        return f"deleted {path}"

    script = [
        {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "d1", "type": "function",
            "function": {"name": "delete_file", "arguments": {"path": str(target)}},
        }]}, "finish_reason": "tool_calls"},
        {"message": {"role": "assistant", "content": "好的，我没动它。"}, "finish_reason": "stop"},
    ]
    mock = MockLLMClient(script=script)
    loop = AgentLoop(registry, EngineRouter(mock), _memory())
    outcome = loop.run("删掉那份报告", TurnContext("删掉报告", gate=safety))

    assert len(seen) == 1, "确认弹窗未触发"
    assert str(target.resolve()) in seen[0]["targets"], f"弹窗未显示完整绝对路径：{seen[0]['targets']}"
    assert Path(seen[0]["targets"][0]).is_absolute(), "弹窗路径不是绝对路径"
    assert calls == [], "用户取消后仍执行了删除"
    assert outcome.trace[0]["cancelled"] is True
    assert outcome.answer == "好的，我没动它。", "取消后未继续跑完本轮"
    assert target.exists(), "取消后文件被删"
    # 第二轮的 messages 里必须能看到被回灌的取消结果
    tool_messages = [m for m in mock.received_messages[1] if m.get("role") == "tool"]
    assert tool_messages, "取消结果未回灌给模型"
    assert "取消" in str(tool_messages[0]), f"回灌内容未说明取消：{tool_messages[0]}"
