"""QA 独立验证：畸形/恶意 LLM 输出、工具异常、降级路径、异常输入 —— 一律不得崩溃。"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from jarvis_console.core.agent_loop import AgentLoop
from jarvis_console.core.errors import ConfigError, LLMError
from jarvis_console.core.llm_client import DeepSeekClient, EngineRouter, LLMClient, MockLLMClient, OllamaClient
from jarvis_console.core.memory import MemoryStore
from jarvis_console.core.tool_registry import ToolRegistry
from jarvis_console.core.types import LLMResponse, ToolCall, TurnContext

_TMP = Path(tempfile.mkdtemp(prefix="qa_robust_"))
_seq = iter(range(10000))


def _memory() -> MemoryStore:
    return MemoryStore(_TMP / f"mem{next(_seq)}.db", ollama_base_url="http://127.0.0.1:1")


class _FakeResponse:
    def __init__(self, data: Any, ok: bool = True) -> None:
        self._data = data
        self.ok = ok

    def raise_for_status(self) -> None:
        if not self.ok:
            import requests

            raise requests.HTTPError("http error")

    def json(self) -> Any:
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class _FakeSession:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.cursor = 0
        self.sent: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        del url
        self.sent.append(kwargs.get("json") or {})
        if self.cursor >= len(self.script):
            return _FakeResponse({})
        item = self.script[self.cursor]
        self.cursor += 1
        return _FakeResponse(item)

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        del url, kwargs
        return _FakeResponse({"models": []})


# ---------------------------------------------------------------- 畸形 LLM 响应


MALFORMED = {
    "empty_object": {},
    "no_choices": {"id": "x", "object": "chat.completion"},
    "empty_choices": {"choices": []},
    "choices_not_list": {"choices": {"0": 1}},
    "message_missing": {"choices": [{"finish_reason": "stop"}]},
    "message_null": {"choices": [{"message": None}]},
    "message_is_string": {"choices": [{"message": "hello"}]},
    "content_null": {"choices": [{"message": {"role": "assistant", "content": None}}]},
    "tool_calls_not_list": {"choices": [{"message": {"content": "x", "tool_calls": "oops"}}]},
    "tool_call_element_null": {"choices": [{"message": {"content": "x", "tool_calls": [None]}}]},
    "tool_call_no_function": {"choices": [{"message": {"content": "x", "tool_calls": [{"id": "c"}]}}]},
    "usage_not_dict": {"choices": [{"message": {"content": "x"}}], "usage": "free"},
    "usage_negative": {"choices": [{"message": {"content": "x"}}], "usage": {"prompt_tokens": -5}},
    "error_envelope": {"error": {"message": "Insufficient balance", "type": "invalid_request_error"}},
}


@pytest.mark.parametrize("label,payload", list(MALFORMED.items()), ids=list(MALFORMED))
def test_malformed_deepseek_payloads_never_crash(label: str, payload: dict) -> None:
    """畸形响应只能有两种结局：收敛成 LLMError，或降级成一个自洽的空响应。
    绝不允许抛裸 KeyError/TypeError/AttributeError/IndexError 给 UI 线程。"""
    session = _FakeSession([payload])
    client = DeepSeekClient("k", session=session)
    try:
        response = client.chat([{"role": "user", "content": "x"}], [])
    except LLMError as exc:
        assert str(exc), "LLMError 不能是空消息"
        return
    # 走到这里说明是"降级为可用响应"：字段类型必须自洽，不得出现半截数据
    assert isinstance(response.content, str), f"{label}: content 不是字符串"
    assert isinstance(response.tool_calls, list), f"{label}: tool_calls 不是列表"
    assert response.finish_reason in {"stop", "tool_calls"}
    assert isinstance(response.prompt_tokens, int) and isinstance(response.completion_tokens, int)


@pytest.mark.xfail(strict=False, reason="已知缺口：无 function 字段的 tool_call 未被过滤，产生空名调用")
def test_tool_call_without_function_is_dropped() -> None:
    """{"id": "c"} 缺少 function 时，应整条丢弃；现状产出 name='' 的幽灵调用，
    白白消耗一轮去换一个 ToolNotFound。"""
    session = _FakeSession([{"choices": [{
        "message": {"content": "x", "tool_calls": [{"id": "c"}]},
        "finish_reason": "tool_calls",
    }]}])
    response = DeepSeekClient("k", session=session).chat([{"role": "user", "content": "x"}], [])
    assert response.tool_calls == [], f"未过滤空名调用：{response.tool_calls}"


@pytest.mark.xfail(strict=False, reason="已知缺口：negative token 计数未夹紧，UI 会显示负数用量")
def test_negative_usage_is_clamped() -> None:
    session = _FakeSession([{"choices": [{"message": {"content": "x"}}], "usage": {"prompt_tokens": -5}}])
    response = DeepSeekClient("k", session=session).chat([{"role": "user", "content": "x"}], [])
    assert response.prompt_tokens >= 0, f"token 计数为负：{response.prompt_tokens}"


def test_non_dict_json_body_raises_llmerror() -> None:
    session = _FakeSession([["not", "an", "object"]])
    client = DeepSeekClient("k", session=session)
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}], [])


def test_invalid_json_body_raises_llmerror() -> None:
    import json

    session = _FakeSession([json.JSONDecodeError("bad", "doc", 0)])
    client = DeepSeekClient("k", session=session)
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}], [])


def test_http_error_is_normalized() -> None:
    import requests

    class _Boom:
        def __init__(self) -> None:
            self.ok = False

        def raise_for_status(self) -> None:
            raise requests.HTTPError("500 boom")

        def json(self) -> dict:
            return {}

    class _S:
        def post(self, *a: Any, **k: Any) -> _Boom:
            del a, k
            return _Boom()

        def get(self, *a: Any, **k: Any) -> _Boom:
            del a, k
            return _Boom()

    client = DeepSeekClient("k", session=_S())  # type: ignore[arg-type]
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}], [])


@pytest.mark.xfail(strict=False, reason="已知缺口：未解析 DeepSeek error 信封，真实原因被吞掉")
def test_error_envelope_reason_is_surfaced() -> None:
    """DeepSeek 常以 HTTP 200 + {"error": {...}} 返回余额不足/鉴权失败。
    期望：把 error.message 透传给用户；现状：只报「响应缺少 choices」。"""
    session = _FakeSession([MALFORMED["error_envelope"]])
    client = DeepSeekClient("k", session=session)
    with pytest.raises(LLMError) as info:
        client.chat([{"role": "user", "content": "x"}], [])
    assert "Insufficient balance" in str(info.value), "真实失败原因被吞掉，排障成本极高"


def test_tool_arguments_invalid_json_string() -> None:
    """arguments 是非法 JSON 字符串时：ToolCall.from_api 抛 ValueError。
    该异常不是 LLMError，因此 EngineRouter 不会触发兜底引擎（观察项）。"""
    session = _FakeSession([{"choices": [{
        "message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "ping", "arguments": "{not json"},
        }]},
        "finish_reason": "tool_calls",
    }]}])
    client = DeepSeekClient("k", session=session)
    with pytest.raises(ValueError):
        client.chat([{"role": "user", "content": "x"}], [])


def test_agent_loop_contains_invalid_tool_arguments_without_crash() -> None:
    """即便参数解析炸了，AgentLoop 也必须收敛为 outcome.error，不能把异常抛给 UI 线程。"""
    registry = ToolRegistry()

    @registry.register("ping", "ping", "test")
    def ping() -> str:
        return "pong"

    class _BoomClient(LLMClient):
        name = "Boom"

        def available(self) -> bool:
            return True

        def chat(self, messages: list[dict], tools: list[dict], timeout: int = 120) -> LLMResponse:
            del messages, tools, timeout
            raise ValueError("工具参数不是合法 JSON")

    loop = AgentLoop(registry, EngineRouter(_BoomClient()), _memory())
    outcome = loop.run("x", TurnContext("x"))
    assert outcome.error, "异常未收敛到 outcome.error"
    assert "合法 JSON" in outcome.error


# ---------------------------------------------------------------- 工具层异常


def test_unknown_tool_name_is_a_failed_result_not_a_crash() -> None:
    registry = ToolRegistry()
    result = registry.invoke(ToolCall("c1", "no_such_tool", {}), TurnContext("x"))
    assert result.ok is False
    assert "不存在" in result.error


def test_wrong_argument_types_and_extra_args_are_contained() -> None:
    registry = ToolRegistry()

    @registry.register("echo", "echo", "test")
    def echo(text: str) -> str:
        return text

    ctx = TurnContext("x")
    bad = registry.invoke(ToolCall("c1", "echo", {"text": "a", "unexpected": 1}), ctx)
    assert bad.ok is False, "多余参数应被拒，而不是静默忽略"
    missing = registry.invoke(ToolCall("c1", "echo", {}), ctx)
    assert missing.ok is False, "缺参数应被拒"


def test_tool_raising_exception_becomes_failed_result_and_round_continues() -> None:
    registry = ToolRegistry()

    @registry.register("boom", "boom", "test")
    def boom() -> str:
        raise RuntimeError("工具内部炸了")

    script = [
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "b1", "type": "function", "function": {"name": "boom", "arguments": {}}},
        ]}, "finish_reason": "tool_calls"},
        {"message": {"role": "assistant", "content": "工具失败了，我换个办法。"}, "finish_reason": "stop"},
    ]
    loop = AgentLoop(registry, EngineRouter(MockLLMClient(script=script)), _memory())
    outcome = loop.run("做事", TurnContext("做事"))
    assert outcome.error == ""
    assert outcome.trace[0]["ok"] is False
    assert "工具内部炸了" in outcome.trace[0]["error"]
    assert outcome.answer == "工具失败了，我换个办法。", "工具异常后整轮被炸掉"


def test_tool_returning_non_dict_is_normalized() -> None:
    registry = ToolRegistry()

    @registry.register("plain", "plain", "test")
    def plain() -> str:
        return "纯字符串结果"

    result = registry.invoke(ToolCall("c1", "plain", {}), TurnContext("x"))
    assert result.ok is True
    assert result.text == "纯字符串结果"


def test_max_rounds_exhausted_is_reported_not_silently_truncated() -> None:
    registry = ToolRegistry()

    @registry.register("ping", "ping", "test")
    def ping() -> str:
        return "pong"

    script = [{"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"p{i}", "type": "function", "function": {"name": "ping", "arguments": {}}},
    ]}, "finish_reason": "tool_calls"} for i in range(20)]
    loop = AgentLoop(registry, EngineRouter(MockLLMClient(script=script)), _memory(), max_rounds=3)
    outcome = loop.run("一直调用", TurnContext("一直调用"))
    assert outcome.reached_limit is True
    assert outcome.rounds == 3
    assert "已达上限 3 轮" in outcome.answer


def test_max_rounds_is_clamped() -> None:
    registry = ToolRegistry()
    loop = AgentLoop(registry, EngineRouter(MockLLMClient(script=[])), _memory(), max_rounds=999)
    assert loop.max_rounds == 16, "max_rounds 未夹紧到 16"
    assert AgentLoop(registry, EngineRouter(MockLLMClient(script=[])), _memory(), max_rounds=0).max_rounds == 1


def test_cancel_before_start_yields_clean_outcome() -> None:
    registry = ToolRegistry()
    loop = AgentLoop(registry, EngineRouter(MockLLMClient(script=[])), _memory())
    ctx = TurnContext("x")
    ctx.cancel()
    outcome = loop.run("x", ctx)
    assert outcome.cancelled is True
    assert outcome.answer


# ---------------------------------------------------------------- 降级路径


def test_missing_api_key_is_config_error_not_crash() -> None:
    client = DeepSeekClient("")
    assert client.available() is False
    with pytest.raises(ConfigError) as info:
        client.chat([{"role": "user", "content": "x"}], [])
    assert "DEEPSEEK_API_KEY" in str(info.value)


def test_router_falls_back_when_primary_unavailable() -> None:
    class _Ok(LLMClient):
        name = "Local"

        def available(self) -> bool:
            return True

        def chat(self, messages: list[dict], tools: list[dict], timeout: int = 120) -> LLMResponse:
            del messages, tools, timeout
            return LLMResponse(content="本地兜底", engine=self.name)

    router = EngineRouter(DeepSeekClient(""), _Ok())
    response = router.chat([{"role": "user", "content": "x"}], [])
    assert response.content == "本地兜底"
    assert router.active.name == "Local"


def test_primary_fails_then_fallback_fails_gives_combined_error() -> None:
    """主引擎"有 Key 但请求失败"时，兜底引擎也失败，必须给出合并后的可读错误。"""

    class _CloudDown(LLMClient):
        name = "Cloud"

        def available(self) -> bool:
            return True

        def chat(self, messages: list[dict], tools: list[dict], timeout: int = 120) -> LLMResponse:
            del messages, tools, timeout
            raise LLMError("云端 502")

    class _LocalDown(LLMClient):
        name = "Local"

        def available(self) -> bool:
            return True

        def chat(self, messages: list[dict], tools: list[dict], timeout: int = 120) -> LLMResponse:
            del messages, tools, timeout
            raise LLMError("本地也挂了")

    router = EngineRouter(_CloudDown(), _LocalDown(), fail_threshold=1)
    with pytest.raises(LLMError) as info:
        router.chat([{"role": "user", "content": "x"}], [])
    message = str(info.value)
    assert "502" in message and "本地也挂了" in message, f"未合并两侧原因：{message}"


def test_primary_unavailable_then_fallback_fails_is_still_actionable() -> None:
    """主引擎根本没配 Key 时直接走兜底；兜底失败也必须给非空可读错误（不要求合并）。"""

    class _LocalDown(LLMClient):
        name = "Local"

        def available(self) -> bool:
            return True

        def chat(self, messages: list[dict], tools: list[dict], timeout: int = 120) -> LLMResponse:
            del messages, tools, timeout
            raise LLMError("Ollama 未启动")

    router = EngineRouter(DeepSeekClient(""), _LocalDown())
    with pytest.raises(LLMError) as info:
        router.chat([{"role": "user", "content": "x"}], [])
    assert "Ollama 未启动" in str(info.value)


def test_ollama_unreachable_degrades_without_crash() -> None:
    import requests

    class _Refused:
        def __init__(self) -> None:
            self.ok = False

        def raise_for_status(self) -> None:
            raise requests.ConnectionError("refused")

        def json(self) -> dict:
            return {}

    class _S:
        def post(self, *a: Any, **k: Any) -> _Refused:
            del a, k
            return _Refused()

        def get(self, *a: Any, **k: Any) -> _Refused:
            del a, k
            return _Refused()

    client = OllamaClient(session=_S())  # type: ignore[arg-type]
    assert client.available() is False
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}], [])


def test_agent_loop_survives_total_llm_failure() -> None:
    class _Down(LLMClient):
        name = "Down"

        def available(self) -> bool:
            return True

        def chat(self, messages: list[dict], tools: list[dict], timeout: int = 120) -> LLMResponse:
            del messages, tools, timeout
            raise LLMError("全挂")

    loop = AgentLoop(ToolRegistry(), EngineRouter(_Down()), _memory())
    outcome = loop.run("x", TurnContext("x"))
    assert outcome.answer, "全引擎失败时也必须给人类可读答复"
    assert outcome.error


# ---------------------------------------------------------------- 异常输入


@pytest.mark.parametrize("text", ["", "   ", "\n\t", "？", "🙂" * 50, "啊" * 5000, "\x00\x01", "'; DROP TABLE notes;--"])
def test_weird_user_input_never_crashes(text: str) -> None:
    registry = ToolRegistry()

    @registry.register("ping", "ping", "test")
    def ping() -> str:
        return "pong"

    script = [{"message": {"role": "assistant", "content": "收到", "tool_calls": [
        {"id": "p1", "type": "function", "function": {"name": "ping", "arguments": {}}},
    ]}, "finish_reason": "tool_calls"},
        {"message": {"role": "assistant", "content": "已完成"}, "finish_reason": "stop"}]
    loop = AgentLoop(registry, EngineRouter(MockLLMClient(script=script)), _memory())
    outcome = loop.run(text, TurnContext(text))
    assert outcome.error == ""
    assert outcome.answer == "已完成"


def test_extremely_long_tool_output_is_not_truncated_by_loop() -> None:
    """超长工具输出必须原样进入 trace（截断由 read_file 负责，主循环不得悄悄吞掉）。"""
    registry = ToolRegistry()

    @registry.register("huge", "huge", "test")
    def huge() -> str:
        return "A" * 200000

    script = [
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "h1", "type": "function", "function": {"name": "huge", "arguments": {}}},
        ]}, "finish_reason": "tool_calls"},
        {"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"},
    ]
    loop = AgentLoop(registry, EngineRouter(MockLLMClient(script=script)), _memory())
    outcome = loop.run("x", TurnContext("x"))
    assert outcome.error == ""
    assert len(outcome.trace[0]["text"]) == 200000
