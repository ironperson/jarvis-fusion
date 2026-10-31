from __future__ import annotations

import pytest
from typing import Any

from jarvis_console.core.errors import LLMError
from jarvis_console.core.llm_client import (
    DeepSeekClient,
    EngineRouter,
    LLMClient,
    MockLLMClient,
    OllamaClient,
    OpenAICompatibleClient,
    build_engine_list,
)
from jarvis_console.core.message_builder import MessageBuilder
from jarvis_console.core.types import LLMResponse, ToolResult
from jarvis_console.config import ProviderConfig, ProvidersConfig


class FakeResponse:
    def __init__(self, data: dict[str, Any], ok: bool = True) -> None:
        self._data = data
        self.ok = ok

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError("http error")

    def json(self) -> dict[str, Any]:
        return self._data


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.last_json: dict[str, Any] = {}

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        del url
        self.last_json = dict(kwargs.get("json") or {})
        return self.response

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        del url, kwargs
        return FakeResponse({"models": []})


def test_deepseek_strips_reasoning_and_disables_thinking() -> None:
    session = FakeSession(FakeResponse({
        "choices": [{
            "message": {"role": "assistant", "content": "ok", "reasoning_content": "secret"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }))
    client = DeepSeekClient("test-key", session=session)
    response = client.chat([{"role": "user", "content": "hi"}], [])
    assert response.reasoning == "secret"
    assert response.content == "ok"
    assert session.last_json["thinking"] == {"type": "disabled"}
    assert "reasoning_content" not in repr(session.last_json["messages"])


def test_ollama_tool_call_mapping() -> None:
    session = FakeSession(FakeResponse({
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "ping", "arguments": {"x": 1}}}],
        },
        "prompt_eval_count": 4,
        "eval_count": 2,
    }))
    client = OllamaClient(session=session)
    response = client.chat([{"role": "user", "content": "go"}], [])
    assert response.tool_calls[0].name == "ping"
    assert response.finish_reason == "tool_calls"


def test_mock_script_is_sequential_and_sanitized() -> None:
    mock = MockLLMClient(script=[
        {"message": {"role": "assistant", "content": "one", "reasoning_content": "private"}},
        {"message": {"role": "assistant", "content": "two"}},
    ])
    first = mock.chat([{"role": "user", "content": "x"}], [])
    second = mock.chat([{"role": "user", "content": "y"}], [])
    assert first.reasoning == "private"
    assert second.content == "two"
    assert mock.cursor == 2


class FailingClient(LLMClient):
    name = "Failing"

    def available(self) -> bool:
        return True

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], timeout: int = 120) -> LLMResponse:
        del messages, tools, timeout
        raise LLMError("primary down")


class WorkingClient(LLMClient):
    name = "Working"

    def available(self) -> bool:
        return True

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], timeout: int = 120) -> LLMResponse:
        del messages, tools, timeout
        return LLMResponse(content="fallback", engine=self.name)


def test_router_falls_back_and_switches_after_threshold() -> None:
    fallback = WorkingClient()
    router = EngineRouter(FailingClient(), fallback, fail_threshold=1)
    response = router.chat([{"role": "user", "content": "x"}], [])
    assert response.content == "fallback"
    assert router.active is fallback


def test_deepseek_reasoning_and_tool_calls_simultaneous() -> None:
    """DeepSeek 思考模式下 reasoning_content 与 tool_calls 会同时出现（真实形态），
    arguments 是 JSON 字符串而非 dict；两者都不能丢，且出站消息不得含 reasoning_content。"""
    session = FakeSession(FakeResponse({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "我先搜一下",
                "reasoning_content": "用户要 AI 视频，我应该先搜索",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search_bilibili", "arguments": '{"keyword": "AI", "limit": 5}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }))
    client = DeepSeekClient("test-key", session=session)
    response = client.chat([{"role": "user", "content": "找 AI 视频"}], [])
    assert response.content == "我先搜一下"
    assert response.reasoning == "用户要 AI 视频，我应该先搜索"
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.name == "search_bilibili"
    assert call.arguments == {"keyword": "AI", "limit": 5}
    # 出站消息必须不含 reasoning_content（三层防御之请求侧）
    assert "reasoning_content" not in repr(session.last_json["messages"])


def test_reasoning_not_leaked_across_three_rounds() -> None:
    """连续三轮模型都带 reasoning_content，但回灌给模型的消息始终干净。"""
    def make_response(round_idx: int) -> dict[str, Any]:
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": f"第 {round_idx} 步",
                    "reasoning_content": f"私密思考 {round_idx}",
                    "tool_calls": [{
                        "id": f"call_{round_idx}",
                        "type": "function",
                        "function": {"name": "search_bilibili", "arguments": '{"keyword": "AI"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    builder = MessageBuilder([{"role": "user", "content": "找 AI 视频"}])
    for round_idx in range(3):
        # chat() 内部会 assert_no_reasoning，若快照含泄露字段会直接抛错
        client = DeepSeekClient("test-key", session=FakeSession(FakeResponse(make_response(round_idx))))
        response = client.chat(builder.snapshot(), [])
        builder.add_assistant(response)
        builder.add_tool_results([ToolResult(
            tool_call_id=f"call_{round_idx}",
            name="search_bilibili",
            ok=True,
            text="结果",
        )])
        assert "reasoning_content" not in repr(builder.snapshot())
    assert len(builder.messages) == 7  # user + 3*(assistant + tool)


def test_openai_compatible_client_maps_response() -> None:
    """通义/Kimi/智谱/OpenAI 等 OpenAI 兼容端点复用同一封装，正确映射 tool_calls。"""
    session = FakeSession(FakeResponse({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "好",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "ping", "arguments": '{"x": 2}'}}],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }))
    client = OpenAICompatibleClient(
        provider_id="kimi", name="Kimi",
        base_url="https://api.moonshot.cn/v1", model="moonshot-v1-8k",
        api_key="kimi-key", session=session,
    )
    assert client.available() is True
    response = client.chat([{"role": "user", "content": "hi"}], [])
    assert response.tool_calls[0].name == "ping"
    assert response.engine == "Kimi"
    assert client.provider_id == "kimi"


def test_openai_compatible_client_unavailable_without_key() -> None:
    client = OpenAICompatibleClient(
        provider_id="openai", name="OpenAI",
        base_url="https://api.openai.com/v1", model="gpt-4o-mini",
        api_key="", requires_key=True,
    )
    assert client.available() is False


def _sample_providers() -> ProvidersConfig:
    return ProvidersConfig(
        default="deepseek",
        auto=True,
        entries={
            "deepseek": ProviderConfig(id="deepseek", label="DeepSeek",
                                       base_url="https://api.deepseek.com",
                                       model="deepseek-chat", env_key="DEEPSEEK_API_KEY",
                                       priority=10, needs_key=True),
            "kimi": ProviderConfig(id="kimi", label="Kimi",
                                   base_url="https://api.moonshot.cn/v1",
                                   model="moonshot-v1-8k", env_key="MOONSHOT_API_KEY",
                                   priority=30, needs_key=True),
            "ollama": ProviderConfig(id="ollama", label="Ollama",
                                     base_url="http://localhost:11434/v1",
                                     model="qwen3-14b", env_key="",
                                     priority=90, needs_key=False),
        },
    )


def test_router_n_way_select_and_fallback(monkeypatch) -> None:
    """N 路路由：select 切换到指定服务商；主引擎不可用时按优先级降级。"""
    providers = _sample_providers()
    clients = build_engine_list(providers)
    # 把云端两家都设为无密钥（不可用），Ollama available() 取决于本地服务，这里强制可用。
    for client in clients:
        if client.provider_id in {"deepseek", "kimi"}:
            monkeypatch.setattr(client, "available", lambda: False)
        else:
            monkeypatch.setattr(client, "available", lambda: True)
    router = EngineRouter(engines=clients, fail_threshold=1)
    # 默认主引擎应为 deepseek，但不可用，首个可用引擎是 ollama。
    assert router.engines[0].provider_id == "deepseek"
    # select 到 kimi（不可用），返回 True 表示切换指令已接受。
    assert router.select("kimi") is True
    assert router.active.provider_id == "kimi"
    # 未知服务商返回 False。
    assert router.select("nope") is False


def test_router_falls_back_through_priority() -> None:
    """主引擎与次引擎都失败，最终落到第三个可用引擎。"""
    first = FailingClient()
    second = FailingClient()
    third = WorkingClient()
    router = EngineRouter(engines=[first, second, third], fail_threshold=1)
    response = router.chat([{"role": "user", "content": "x"}], [])
    assert response.content == "fallback"
    assert router.active is third


def test_router_collects_all_engine_errors_on_total_failure() -> None:
    """多引擎全部失败时，最终异常必须累积并合并所有引擎的错误信息（而非只留最后一个）。

    回归点：缺陷修复前只保留 ``last_error``，丢掉了前序引擎的具体失败原因；
    修复后 ``chat`` 用 ``errors`` 列表收集每个引擎 ``{name}: {exc}`` 并 ``；`` 拼接。
    """

    class NamedFailingClient(FailingClient):
        def __init__(self, label: str, reason: str) -> None:
            self.name = label
            self._reason = reason

        def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], timeout: int = 120) -> LLMResponse:
            del messages, tools, timeout
            raise LLMError(self._reason)

    first = NamedFailingClient("EngineA", "API 网关 503")
    second = NamedFailingClient("EngineB", "密钥校验失败")
    third = NamedFailingClient("EngineC", "本地服务未启动")
    router = EngineRouter(engines=[first, second, third], fail_threshold=1)
    with pytest.raises(LLMError) as exc_info:
        router.chat([{"role": "user", "content": "x"}], [])
    message = str(exc_info.value)
    assert "EngineA" in message and "API 网关 503" in message
    assert "EngineB" in message and "密钥校验失败" in message
    assert "EngineC" in message and "本地服务未启动" in message
    # 必须显式出现「全部失败」聚合语义，且三个引擎名都在同一句信息里。
    assert "所有服务商均不可用" in message
