"""QA 提出的 4 处源码缺陷的回归测试（修好后须全绿）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis_console.core.errors import LLMError
from jarvis_console.core.llm_client import (
    DeepSeekClient,
    MockLLMClient,
    OllamaClient,
    _parse_tool_calls,
)
from jarvis_console.core.safety import SafetyGate
from jarvis_console.core.tool_registry import REGISTRY
from jarvis_console.core.types import ToolCall, TurnContext


def test_close_app_fallback_shows_absolute_path() -> None:
    """未命中进程时，弹窗目标必须是完整绝对路径或明确的未定位说明。"""
    resolved = SafetyGate._resolve_executable_path("python")
    assert resolved, "必须解析出路径或明确说明"
    assert "未能定位" in resolved or Path(resolved).is_absolute()
    if "未能定位" not in resolved:
        assert Path(resolved).suffix.lower() == ".exe"

    gate = SafetyGate()
    targets = gate.resolve_targets("close_app", {"name": "python"})
    assert targets
    first = targets[0]
    assert isinstance(first, dict)
    path_text = str(first["path"])
    assert "未能定位" in path_text or Path(path_text).is_absolute(), "不得回显裸文件名"
    assert not path_text.lower().endswith(("notepad.exe", "python")) or "未能定位" in path_text
    # pid 类型统一为 int：-1 表示未找到，展示字符串另放 pid_display
    assert isinstance(first.get("pid"), int)
    assert isinstance(first.get("pid_display"), str)


def test_deepseek_error_envelope_is_surfaced() -> None:
    """HTTP 200 + error 信封必须透传真实原因，不能被 '缺少 choices' 吞掉。"""

    class FakeResponse:
        def __init__(self, data: dict[str, Any]) -> None:
            self._data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._data

    class FakeSession:
        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            del url, kwargs
            return FakeResponse({"error": {"message": "Insufficient balance", "type": "auth"}})

    client = DeepSeekClient("k", session=FakeSession())
    with pytest.raises(LLMError) as caught:
        client.chat([{"role": "user", "content": "hi"}], [])
    assert "Insufficient balance" in str(caught.value)
    assert "缺少 choices" not in str(caught.value)


def test_ghost_tool_call_is_filtered() -> None:
    calls = _parse_tool_calls({
        "tool_calls": [
            {"id": "ghost"},
            {"id": "empty_name", "function": {"name": "   ", "arguments": "{}"}},
            {"id": "ok", "function": {"name": "ping", "arguments": "{\"a\": 1}"}},
            "not-a-dict",
        ]
    })
    assert [call.name for call in calls] == ["ping"]
    assert isinstance(calls[0], ToolCall)


def test_negative_usage_is_clamped() -> None:
    client = MockLLMClient(script=[
        {
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
            "usage": {"prompt_tokens": -5, "completion_tokens": -9},
        }
    ])
    response = client.chat([{"role": "user", "content": "x"}], [])
    assert response.prompt_tokens == 0
    assert response.completion_tokens == 0


def test_ollama_thinking_field_is_captured_and_not_leaked() -> None:
    """Ollama 思考模型的键名是 thinking（不是 reasoning_content），必须也接住。"""
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "message": {"role": "assistant", "content": "答案", "thinking": "本地思考链"},
                "prompt_eval_count": -3,
                "eval_count": 7,
            }

    class FakeSession:
        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            del url, kwargs
            return FakeResponse()

    response = OllamaClient(session=FakeSession()).chat([{"role": "user", "content": "q"}], [])
    assert response.had_reasoning is True
    assert response.reasoning == "本地思考链"
    assert response.prompt_tokens == 0 and response.completion_tokens == 7
    # 思考内容只进 UI 字段，不参与协议往返
    assert response.reasoning == "本地思考链"
    assert not hasattr(response, "thinking")


def test_read_file_refuses_secret_files(tmp_path: Path) -> None:
    """Key 落盘后不能被模型读进上下文发往云端。"""
    import jarvis_console.tools  # noqa: F401

    secret = tmp_path / ".env"
    secret.write_text("DEEPSEEK_API_KEY=sk-plain-text-key", encoding="utf-8")
    result = REGISTRY.invoke(ToolCall("c1", "read_file", {"path": str(secret)}), TurnContext("read"))
    assert result.ok is False
    assert "sk-plain-text-key" not in result.text
    assert "拒绝读取密钥" in result.error or "拒绝读取密钥" in result.text

    listed = REGISTRY.invoke(
        ToolCall("c2", "find_files", {"pattern": "*", "root": str(tmp_path), "limit": 20}),
        TurnContext("list"),
    )
    assert listed.ok
    assert str(secret) not in listed.text
    assert "已隐藏" in listed.text
