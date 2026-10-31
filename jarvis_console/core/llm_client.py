"""DeepSeek、Ollama 与离线 Mock 的统一 LLM 客户端。"""
from __future__ import annotations

import copy
import json
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

import requests

from ..config import get_secret, has_secret
from ..logging_setup import get_logger, redact
from ..paths import FIXTURES_DIR
from .errors import ConfigError, LLMError
from .message_builder import MessageBuilder
from .types import LLMResponse, ToolCall

log = get_logger(__name__)


class LLMClient(ABC):
    """同步 LLM 客户端抽象，调用方必须在工作线程执行。"""

    name: str = "unknown"

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout: int = 120,
    ) -> LLMResponse:
        """执行一次工具调用兼容的对话请求。"""
        raise NotImplementedError("子类必须实现 chat")

    @abstractmethod
    def available(self) -> bool:
        """快速检查引擎是否具备最小调用条件。"""
        raise NotImplementedError("子类必须实现 available")


def _parse_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    """解析工具调用；丢弃无 function 或空名的幽灵调用，避免白耗一轮。"""
    calls: list[ToolCall] = []
    for raw_call in message.get("tool_calls") or []:
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function")
        if not isinstance(function, dict) or not str(function.get("name") or "").strip():
            log.warning("忽略无 function 名称的工具调用：%s", redact(raw_call))
            continue
        calls.append(ToolCall.from_api(raw_call))
    return calls


def _raise_api_error(data: dict[str, Any], engine: str) -> None:
    """OpenAI 兼容接口常以 HTTP 200 + error 信封返回业务失败，必须透传原因。"""
    error = data.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("code") or "")
        if message:
            raise LLMError(f"{engine} 返回错误：{message}")
    elif isinstance(error, str) and error.strip():
        raise LLMError(f"{engine} 返回错误：{error.strip()}")


def _usage_count(usage: dict[str, Any], key: str) -> int:
    """读取 token 计数并夹到非负，避免 UI 显示负数。"""
    try:
        return max(0, int(usage.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _openai_response(data: dict[str, Any], engine: str) -> LLMResponse:
    _raise_api_error(data, engine)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError(f"{engine} 响应缺少 choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise LLMError(f"{engine} choices[0] 格式错误")
    raw_message = choice.get("message")
    if not isinstance(raw_message, dict):
        raise LLMError(f"{engine} 响应缺少 message")
    reasoning = str(raw_message.get("reasoning_content") or raw_message.get("thinking") or "")
    clean_message = MessageBuilder.sanitize_assistant_message(raw_message)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return LLMResponse(
        content=str(clean_message.get("content") or ""),
        tool_calls=_parse_tool_calls(clean_message),
        reasoning=reasoning,
        finish_reason=str(choice.get("finish_reason") or ("tool_calls" if clean_message.get("tool_calls") else "stop")),
        engine=engine,
        prompt_tokens=_usage_count(usage, "prompt_tokens"),
        completion_tokens=_usage_count(usage, "completion_tokens"),
        had_reasoning=bool(reasoning),
    )


class DeepSeekClient(LLMClient):
    """使用 requests 直连 DeepSeek 的 OpenAI 兼容接口。"""

    name = "DeepSeek"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-flash",
        disable_thinking: bool = True,
        session: requests.Session | None = None,
    ) -> None:
        self.provider_id = "deepseek"
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key.strip()
        self.disable_thinking = disable_thinking
        self._session = session or requests.Session()
        self._request_timeout = 120

    def available(self) -> bool:
        """DeepSeek 的最小可用条件是已配置 Key。"""
        return bool(self.api_key)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """发送一次请求并规范化 HTTP/JSON 错误。"""
        if not self.api_key:
            raise ConfigError(
                "没找到 DEEPSEEK_API_KEY。请写入 ~/.jarvis/.env："
                "DEEPSEEK_API_KEY=你的Key；配好后下一条消息即可生效。"
            )
        try:
            response = self._session.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._request_timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise LLMError(f"DeepSeek 请求失败：{type(exc).__name__} {exc}") from exc
        except (ValueError, TypeError) as exc:
            raise LLMError(f"DeepSeek 返回了无效 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise LLMError("DeepSeek 响应不是 JSON 对象")
        return data

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout: int = 120,
    ) -> LLMResponse:
        """调用 deepseek-flash；请求侧尽力显式关闭思考模式。"""
        MessageBuilder.assert_no_reasoning(messages)
        self._request_timeout = max(1, timeout)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": copy.deepcopy(messages),
            "tool_choice": "auto",
        }
        if tools:
            payload["tools"] = copy.deepcopy(tools)
        if self.disable_thinking:
            payload["thinking"] = {"type": "disabled"}
        return _openai_response(self._post(payload), self.name)


class OllamaClient(LLMClient):
    """本地 Ollama 工具调用客户端。"""

    name = "Ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3-14b",
        session: requests.Session | None = None,
    ) -> None:
        self.provider_id = "ollama"
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._session = session or requests.Session()

    def available(self) -> bool:
        """用短超时检查本地 Ollama 服务。"""
        try:
            response = self._session.get(f"{self.base_url}/api/tags", timeout=1.0)
            return bool(response.ok)
        except requests.RequestException:
            return False

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout: int = 120,
    ) -> LLMResponse:
        """调用 Ollama /api/chat 并映射为统一响应。"""
        MessageBuilder.assert_no_reasoning(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": copy.deepcopy(messages),
            "stream": False,
        }
        if tools:
            payload["tools"] = copy.deepcopy(tools)
        try:
            response = self._session.post(
                f"{self.base_url}/api/chat", json=payload, timeout=max(1, timeout)
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise LLMError(f"Ollama 请求失败：{type(exc).__name__} {exc}") from exc
        except (ValueError, TypeError) as exc:
            raise LLMError(f"Ollama 返回了无效 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise LLMError("Ollama 响应不是 JSON 对象")
        _raise_api_error(data, "Ollama")
        raw_message = data.get("message")
        if not isinstance(raw_message, dict):
            raise LLMError("Ollama 响应缺少 message")
        clean_message = MessageBuilder.sanitize_assistant_message(raw_message)
        usage = {"prompt_tokens": data.get("prompt_eval_count", 0), "completion_tokens": data.get("eval_count", 0)}
        calls = _parse_tool_calls(clean_message)
        return LLMResponse(
            content=str(clean_message.get("content") or ""),
            tool_calls=calls,
            reasoning=str(raw_message.get("thinking") or raw_message.get("reasoning_content") or ""),
            had_reasoning=bool(raw_message.get("thinking") or raw_message.get("reasoning_content")),
            finish_reason="tool_calls" if calls else "stop",
            engine=self.name,
            prompt_tokens=_usage_count(usage, "prompt_tokens"),
            completion_tokens=_usage_count(usage, "completion_tokens"),
        )


class MockLLMClient(LLMClient):
    """从离线 JSON 脚本顺序返回响应的确定性客户端。"""

    name = "Mock"

    def __init__(
        self,
        script_path: Path | str | None = None,
        script: list[dict[str, Any]] | None = None,
    ) -> None:
        if script is None:
            path = Path(script_path) if script_path else FIXTURES_DIR / "mock_script.json"
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(parsed, list):
                raise ConfigError("Mock 脚本顶层必须是数组")
            self.script = parsed
        else:
            self.script = copy.deepcopy(script)
        self.provider_id = "mock"
        self.cursor = 0
        self.received_messages: list[list[dict[str, Any]]] = []

    def available(self) -> bool:
        """只要脚本还有响应，Mock 引擎即可调用。"""
        return self.cursor < len(self.script)

    def reset(self) -> None:
        """把脚本游标与请求记录重置到初始状态。"""
        self.cursor = 0
        self.received_messages.clear()

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout: int = 120,
    ) -> LLMResponse:
        """返回脚本下一项，且记录经过防泄漏检查的入站 messages。"""
        del tools, timeout
        MessageBuilder.assert_no_reasoning(messages)
        self.received_messages.append(copy.deepcopy(messages))
        if self.cursor >= len(self.script):
            raise LLMError("Mock LLM 脚本已耗尽")
        entry = copy.deepcopy(self.script[self.cursor])
        self.cursor += 1
        if "choices" in entry:
            return _openai_response(entry, self.name)
        raw_message = entry.get("message", entry)
        if not isinstance(raw_message, dict):
            raise LLMError("Mock 脚本项必须包含 message 对象")
        reasoning = str(raw_message.get("reasoning_content") or raw_message.get("thinking") or "")
        clean = MessageBuilder.sanitize_assistant_message(raw_message)
        calls = _parse_tool_calls(clean)
        usage = entry.get("usage") if isinstance(entry.get("usage"), dict) else {}
        return LLMResponse(
            content=str(clean.get("content") or ""),
            tool_calls=calls,
            reasoning=reasoning,
            finish_reason=str(entry.get("finish_reason") or ("tool_calls" if calls else "stop")),
            engine=self.name,
            prompt_tokens=_usage_count(usage, "prompt_tokens"),
            completion_tokens=_usage_count(usage, "completion_tokens"),
            had_reasoning=bool(reasoning),
        )


class OpenAICompatibleClient(LLMClient):
    """通义 / Kimi / 智谱 / OpenAI 等 OpenAI 兼容端点的统一封装。

    这些服务商都暴露 ``/chat/completions`` 协议，仅 ``base_url``、模型名与密钥来源不同，
    因此用一个类参数化即可，避免为每家复制粘贴近乎一致的请求代码。
    """

    provider_id: str = "openai"

    def __init__(
        self,
        provider_id: str,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        session: requests.Session | None = None,
        priority: int = 100,
        requires_key: bool = True,
        extra_body: dict[str, object] | None = None,
    ) -> None:
        self.provider_id = provider_id.strip().lower()
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = (api_key or "").strip()
        self.priority = priority
        self.requires_key = requires_key
        self.extra_body = dict(extra_body or {})
        self._session = session or requests.Session()
        self._request_timeout = 120

    def available(self) -> bool:
        """云端服务商的最小可用条件是已配置密钥；无需密钥时始终可用。"""
        if self.requires_key:
            return bool(self.api_key)
        return True

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.requires_key and not self.api_key:
            raise ConfigError(
                f"未配置 {self.name} 的密钥。请写入 ~/.jarvis/.env 对应 env_key；"
                "UI 只显示是否已配置，不会回显密钥内容。"
            )
        try:
            response = self._session.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._request_timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise LLMError(f"{self.name} 请求失败：{type(exc).__name__} {exc}") from exc
        except (ValueError, TypeError) as exc:
            raise LLMError(f"{self.name} 返回了无效 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise LLMError(f"{self.name} 响应不是 JSON 对象")
        return data

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout: int = 120,
    ) -> LLMResponse:
        """调用 OpenAI 兼容端点并映射为统一响应。"""
        MessageBuilder.assert_no_reasoning(messages)
        self._request_timeout = max(1, timeout)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": copy.deepcopy(messages),
            "tool_choice": "auto",
        }
        if tools:
            payload["tools"] = copy.deepcopy(tools)
        if self.extra_body:
            payload.update(copy.deepcopy(self.extra_body))
        return _openai_response(self._post(payload), self.name)


def build_engine_list(providers: "ProvidersConfig") -> list[LLMClient]:
    """把多服务商配置实例化为有序的客户端列表（优先级升序）。"""
    clients: list[LLMClient] = []
    for entry in providers.ordered():
        if entry.id == "ollama":
            # Ollama 用原生 /api/chat 客户端（工具调用已验证）；去掉 yaml 里的 /v1 后缀。
            base = entry.base_url
            if base.endswith("/v1"):
                base = base[: -len("/v1")]
            clients.append(OllamaClient(base_url=base or "http://localhost:11434", model=entry.model))
        else:
            clients.append(OpenAICompatibleClient(
                provider_id=entry.id,
                name=entry.label,
                base_url=entry.base_url,
                model=entry.model,
                api_key=get_secret(entry.env_key) if entry.env_key else "",
                priority=entry.priority,
                requires_key=entry.needs_key,
            ))
    return clients


class EngineRouter:
    """N 路优先级路由器；主引擎不可达时按优先级自动降级到下一个可用引擎。

    向后兼容：``EngineRouter(primary, fallback, fail_threshold)`` 仍可用（见既有测试），
    此时等价于只有主+兜两个引擎。融合版推荐用 :meth:`from_providers` 构造多服务商列表。
    """

    def __init__(
        self,
        primary: LLMClient | None = None,
        fallback: LLMClient | None = None,
        fail_threshold: int = 2,
        on_engine_changed: Callable[[dict[str, str]], None] | None = None,
        engines: list[LLMClient] | None = None,
    ) -> None:
        self.fail_threshold = max(1, fail_threshold)
        self._on_engine_changed = on_engine_changed
        self._lock = threading.RLock()
        # 向后兼容：从 primary/fallback 构建引擎列表。
        candidate_engines: list[LLMClient] = []
        if engines:
            candidate_engines = list(engines)
        if primary is not None and primary not in candidate_engines:
            candidate_engines.insert(0, primary)
        if fallback is not None and fallback not in candidate_engines:
            candidate_engines.append(fallback)
        # 同一 provider_id 只保留第一个，避免重复。
        seen: set[str] = set()
        self.engines: list[LLMClient] = []
        for client in candidate_engines:
            pid = getattr(client, "provider_id", client.name)
            if pid in seen:
                continue
            seen.add(pid)
            self.engines.append(client)
        self.primary = self.engines[0] if self.engines else primary
        self.fallback = fallback
        self.active: LLMClient = self.primary if self.primary is not None else (
            self.engines[0] if self.engines else None
        )
        self.fail_streak = 0

    @classmethod
    def from_providers(
        cls,
        providers: "ProvidersConfig",
        timeout: int = 120,
        fail_threshold: int = 2,
        on_engine_changed: Callable[[dict[str, str]], None] | None = None,
    ) -> "EngineRouter":
        """从多服务商配置构造 N 路路由器。"""
        del timeout  # 超时在每次 chat 时传入，这里仅占位保持签名一致。
        engines = build_engine_list(providers)
        router = cls(
            engines=engines,
            fail_threshold=fail_threshold,
            on_engine_changed=on_engine_changed,
        )
        # 默认主引擎：优先取配置 default，否则取优先级最高（列表首个）。
        default_id = (providers.default or "").strip().lower()
        if default_id:
            for client in engines:
                if getattr(client, "provider_id", client.name).lower() == default_id:
                    router.active = client
                    break
        return router

    def _ordered(self) -> list[LLMClient]:
        """返回带优先级的尝试顺序；优先级数字越小越靠前（Ollama 默认靠后）。"""
        return sorted(self.engines, key=lambda item: (getattr(item, "priority", 100), item.name))

    def _find_by_id(self, provider_id: str) -> LLMClient | None:
        target = (provider_id or "").strip().lower()
        for client in self.engines:
            if getattr(client, "provider_id", client.name).lower() == target:
                return client
        return None

    def select(self, provider_id: str) -> bool:
        """手动切换到指定服务商；返回是否切换成功。"""
        client = self._find_by_id(provider_id)
        if client is None:
            log.warning("未找到服务商：%s", provider_id)
            return False
        with self._lock:
            self._switch(client, f"用户手动切换至 {client.name}")
        return True

    def list_engines(self) -> list[dict[str, object]]:
        """返回所有引擎的概览（不含密钥，用于 UI）。"""
        return [
            {
                "id": getattr(client, "provider_id", client.name),
                "name": client.name,
                "available": client.available(),
                "active": client is self.active,
            }
            for client in self.engines
        ]

    def _switch(self, client: LLMClient, cause: str) -> None:
        if self.active is client:
            return
        self.active = client
        log.warning("引擎切换至 %s：%s", client.name, cause)
        if self._on_engine_changed is not None:
            self._on_engine_changed({"engine": client.name, "cause": cause})

    def on_failure(self, err: Exception) -> None:
        """记录连续故障；达到阈值后切到下一个可用引擎。"""
        with self._lock:
            self.fail_streak += 1
            if self.fail_streak >= self.fail_threshold:
                ordered = self._ordered()
                try:
                    current_index = ordered.index(self.active)
                except ValueError:
                    current_index = -1
                for offset in range(1, len(ordered)):
                    candidate = ordered[(current_index + offset) % len(ordered)]
                    if candidate.available():
                        self._switch(candidate, str(err))
                        break

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout: int = 120,
    ) -> LLMResponse:
        """依次尝试当前及后续可用引擎；全部失败才抛出。"""
        ordered = self._ordered()
        if not ordered:
            raise LLMError("没有配置任何大模型服务商。")
        try:
            start_index = ordered.index(self.active)
        except ValueError:
            start_index = 0
        last_error: Exception | None = None
        errors: list[str] = []
        any_available = any(client.available() for client in ordered)
        for offset in range(len(ordered)):
            current = ordered[(start_index + offset) % len(ordered)]
            # 跳过明显未就绪的引擎（除非它是唯一可选）；首个（当前）引擎总是尝试。
            if offset > 0 and not current.available() and any_available:
                continue
            try:
                response = current.chat(messages, tools, timeout)
            except (LLMError, ConfigError) as exc:
                last_error = exc
                errors.append(f"{current.name}: {exc}")
                with self._lock:
                    self.fail_streak += 1
                log.warning("引擎 %s 调用失败：%s", current.name, exc)
                continue
            if current is not self.active:
                self._switch(current, "自动降级或手动切换")
            with self._lock:
                self.fail_streak = 0
            return response
        if last_error is not None:
            detail = "；".join(errors) if errors else str(last_error)
            raise LLMError(f"所有服务商均不可用：{detail}") from last_error
        raise LLMError("没有可用的大模型服务商（请检查密钥或本地 Ollama 是否运行）")
