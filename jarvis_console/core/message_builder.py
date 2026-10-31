"""OpenAI 消息组装与 reasoning_content 三层防御。"""
from __future__ import annotations

import copy
from typing import Any

from .errors import ReasoningLeakError
from .types import LLMResponse, ToolResult


class MessageBuilder:
    """维护模型协议消息，并确保思考内容永不被回传。"""

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.messages: list[dict[str, Any]] = copy.deepcopy(messages or [])

    def add_system(self, text: str) -> None:
        """添加系统消息。"""
        self.messages.append({"role": "system", "content": str(text)})

    def add_user(self, text: str) -> None:
        """添加用户消息。"""
        self.messages.append({"role": "user", "content": str(text)})

    def add_assistant(self, response: LLMResponse) -> None:
        """按白名单从规范化响应构造 assistant 消息。"""
        raw: dict[str, Any] = {"role": "assistant", "content": response.content or ""}
        if response.tool_calls:
            raw["tool_calls"] = [call.to_api() for call in response.tool_calls]
        self.messages.append(self.sanitize_assistant_message(raw))

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """把工具结果逐条回灌给模型。"""
        self.messages.extend(result.to_tool_message() for result in results)

    @staticmethod
    def sanitize_assistant_message(message: dict[str, Any]) -> dict[str, Any]:
        """只保留 role/content/tool_calls，显式丢弃所有思考字段。"""
        sanitized: dict[str, Any] = {
            "role": "assistant",
            "content": str(message.get("content") or ""),
        }
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            sanitized["tool_calls"] = copy.deepcopy(tool_calls)
        return sanitized

    @classmethod
    def assert_no_reasoning(cls, value: Any, path: str = "messages") -> None:
        """递归检查出站对象；发现 reasoning_content 时阻断请求。"""
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if key == "reasoning_content":
                    raise ReasoningLeakError(f"禁止回传 reasoning_content，位置：{child_path}")
                cls.assert_no_reasoning(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                cls.assert_no_reasoning(child, f"{path}[{index}]")

    def to_api_payload(self) -> list[dict[str, Any]]:
        """返回深拷贝的安全出站消息。"""
        payload = copy.deepcopy(self.messages)
        self.assert_no_reasoning(payload)
        return payload

    def snapshot(self) -> list[dict[str, Any]]:
        """返回当前消息历史的只读式深拷贝。"""
        return copy.deepcopy(self.messages)
