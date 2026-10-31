"""内核层跨模块使用的数据类型，保持零 Qt 依赖。"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolCall:
    """一次符合 OpenAI Tool Calls 协议的工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> "ToolCall":
        """从 OpenAI/Ollama 的工具调用字典解析实例。"""
        function = value.get("function", {})
        raw_arguments = function.get("arguments", {})
        if isinstance(raw_arguments, str):
            try:
                parsed = json.loads(raw_arguments or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"工具参数不是合法 JSON：{exc}") from exc
        elif isinstance(raw_arguments, dict):
            parsed = dict(raw_arguments)
        else:
            raise ValueError("工具参数必须是 JSON 对象或 JSON 字符串")
        return cls(
            id=str(value.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
            name=str(function.get("name") or value.get("name") or ""),
            arguments=parsed,
        )

    def to_api(self) -> dict[str, Any]:
        """序列化为 OpenAI assistant.tool_calls 元素。"""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass(slots=True)
class ToolResult:
    """工具执行的规范化结果。"""

    tool_call_id: str
    name: str
    ok: bool
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    elapsed_ms: float = 0.0
    cancelled: bool = False

    def to_tool_message(self) -> dict[str, Any]:
        """转成供模型继续推理的 role=tool 消息。"""
        payload = {
            "ok": self.ok,
            "text": self.text,
            "data": self.data,
            "error": self.error,
            "cancelled": self.cancelled,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        }

    def to_dict(self) -> dict[str, Any]:
        """返回可跨线程传递的基础字典。"""
        return asdict(self)


@dataclass(slots=True)
class LLMResponse:
    """模型响应；reasoning 仅供 UI 展示，不回写 messages。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str = ""
    finish_reason: str = "stop"
    engine: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    had_reasoning: bool = False


@dataclass(slots=True)
class ConfirmRequest:
    """工作线程发给主线程的破坏性操作确认请求。"""

    tool_name: str
    action_text: str
    targets: list[Any] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """返回只包含基础类型的信号载荷。"""
        return asdict(self)


@dataclass(slots=True)
class SysStatus:
    """系统实时状态（每秒采样，零 Qt 依赖、可 JSON 序列化）。"""

    cpu: float = 0.0
    mem: float = 0.0
    net_up: float = 0.0
    net_down: float = 0.0
    battery: float = -1.0
    on_ac: bool = True

    def to_dict(self) -> dict[str, float | bool]:
        """返回仅含基础类型的字典，可供 Qt Signal 载荷与 HUD 桥使用。"""
        return asdict(self)


@dataclass
class TurnContext:
    """单次对话回合上下文，支持线程安全的协作式取消。"""

    user_text: str
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    cancelled: bool = False
    trust_mode: bool = False
    gate: Any = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    memory_hits: dict[str, Any] = field(default_factory=dict)
    _cancel_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)

    def cancel(self) -> None:
        """请求在下一个模型/工具边界终止回合。"""
        self.cancelled = True
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        """返回回合是否已被请求终止。"""
        return self.cancelled or self._cancel_event.is_set()


@dataclass(slots=True)
class TurnOutcome:
    """一次 Agent 循环的最终输出。"""

    answer: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0
    ingested_note_ids: list[str] = field(default_factory=list)
    remembered_ids: list[str] = field(default_factory=list)
    memory_hits: list[dict[str, Any]] = field(default_factory=list)
    engine: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reached_limit: bool = False
    cancelled: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """返回可作为 Qt Signal 载荷的基础字典。"""
        return asdict(self)
