"""类型提示驱动的工具注册表。"""
from __future__ import annotations

import inspect
import json
import time
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Union, get_args, get_origin, get_type_hints

from ..logging_setup import get_logger, redact
from .errors import ToolNotFound
from .types import ToolCall, ToolResult, TurnContext

log = get_logger(__name__)


def _json_type(annotation: Any) -> dict[str, Any]:
    """把常用 Python 类型提示转换为 JSON Schema。"""
    if annotation in (inspect.Signature.empty, Any):
        return {}
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is type(None):
        return {"type": "null"}
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Literal:
        return {"enum": list(arguments)}
    if origin in (list, tuple, set):
        item_type = arguments[0] if arguments else Any
        return {"type": "array", "items": _json_type(item_type)}
    if origin is dict:
        value_type = arguments[1] if len(arguments) > 1 else Any
        return {"type": "object", "additionalProperties": _json_type(value_type)}
    if origin in (Union, types.UnionType):
        schemas = [_json_type(item) for item in arguments]
        return {"anyOf": schemas}
    return {"type": "string"}


def _parameters_schema(function: Callable[..., Any]) -> dict[str, Any]:
    signature = inspect.signature(function)
    try:
        hints = get_type_hints(function)
    except (NameError, TypeError):
        hints = {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        if name.startswith("_"):
            continue
        annotation = hints.get(name, parameter.annotation)
        schema = _json_type(annotation)
        if parameter.default is not inspect.Signature.empty:
            schema["default"] = parameter.default
        else:
            required.append(name)
        properties[name] = schema
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


@dataclass(slots=True)
class ToolSpec:
    """已注册工具的实现与元数据。"""

    name: str
    description: str
    parameters: dict[str, Any]
    category: str
    destructive: bool
    enabled: bool
    fn: Callable[..., Any]

    def to_openai_schema(self) -> dict[str, Any]:
        """返回 OpenAI tools 数组中的 function 项。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """显式工具容器，负责裁剪、校验、门禁、计时与异常归一化。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        description: str,
        category: str,
        destructive: bool = False,
        enabled: bool = True,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """返回把函数注册到当前注册表的装饰器。"""
        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            if name in self._tools:
                existing = self._tools[name]
                if existing.fn is function:
                    return function
                raise ValueError(f"工具已重复注册：{name}")
            self._tools[name] = ToolSpec(
                name=name,
                description=description.strip() or (inspect.getdoc(function) or name),
                parameters=_parameters_schema(function),
                category=category,
                destructive=destructive,
                enabled=enabled,
                fn=function,
            )
            return function
        return decorator

    def get(self, name: str) -> ToolSpec:
        """按名返回已启用工具。"""
        spec = self._tools.get(name)
        if spec is None or not spec.enabled:
            raise ToolNotFound(f"工具不存在或未启用：{name}")
        return spec

    def configure(self, enabled_names: list[str], disabled_names: list[str] | None = None) -> None:
        """按配置白名单裁剪工具；空白名单保留装饰器默认状态。"""
        enabled_set = set(enabled_names)
        disabled_set = set(disabled_names or [])
        for spec in self._tools.values():
            if enabled_set:
                spec.enabled = spec.name in enabled_set
            if spec.name in disabled_set:
                spec.enabled = False

    def enabled_tools(self) -> list[ToolSpec]:
        """按注册顺序返回已启用工具。"""
        return [spec for spec in self._tools.values() if spec.enabled]

    def openai_schemas(self) -> list[dict[str, Any]]:
        """返回所有已启用工具的 OpenAI Schema。"""
        return [spec.to_openai_schema() for spec in self.enabled_tools()]

    def names(self) -> list[str]:
        """返回已启用工具名。"""
        return [spec.name for spec in self.enabled_tools()]

    def invoke(self, call: ToolCall, ctx: TurnContext) -> ToolResult:
        """执行一次工具调用；所有异常均转换为 ToolResult。"""
        started = time.perf_counter()
        result: ToolResult
        try:
            spec = self.get(call.name)
            signature = inspect.signature(spec.fn)
            bound = signature.bind(**call.arguments)
            bound.apply_defaults()
            if spec.destructive and ctx.gate is not None:
                approved = bool(ctx.gate.request(spec.name, dict(bound.arguments), ctx))
                if not approved:
                    elapsed = (time.perf_counter() - started) * 1000
                    result = ToolResult(
                        tool_call_id=call.id,
                        name=call.name,
                        ok=False,
                        error="用户取消了操作，未做任何改动",
                        elapsed_ms=elapsed,
                        cancelled=True,
                    )
                    self._log_invocation(ctx, call, result)
                    return result
            raw_value = spec.fn(**bound.arguments)
            if isinstance(raw_value, dict):
                data = dict(raw_value)
                ok = bool(data.get("ok", True))
                error = str(data.get("error") or data.get("reason") or "") if not ok else ""
                text = str(data.get("text") or data.get("message") or json.dumps(data, ensure_ascii=False, default=str))
            else:
                data = {}
                ok = True
                error = ""
                text = str(raw_value)
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                ok=ok,
                text=text,
                data=data,
                error=error,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                ok=False,
                error=str(exc),
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        self._log_invocation(ctx, call, result)
        return result

    @staticmethod
    def _log_invocation(ctx: TurnContext, call: ToolCall, result: ToolResult) -> None:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "turn_id": ctx.turn_id,
            "tool": call.name,
            "args": redact(json.dumps(call.arguments, ensure_ascii=False, default=str)),
            "ok": result.ok,
            "elapsed_ms": round(result.elapsed_ms, 3),
            "error": result.error,
        }
        log.info("tool_call %s", json.dumps(event, ensure_ascii=False))


REGISTRY = ToolRegistry()


def tool(
    name: str,
    description: str,
    category: str,
    destructive: bool = False,
    enabled: bool = True,
    registry: ToolRegistry = REGISTRY,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """把函数注册到显式 REGISTRY（测试可传独立 registry）。"""
    return registry.register(name, description, category, destructive, enabled)
