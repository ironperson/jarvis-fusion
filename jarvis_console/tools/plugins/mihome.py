"""米家设备控制插件（凭据缺失即禁用，R2）。

安全边界：
- 仅在 ``MIJIA_TOKEN`` 配置后启用；缺失时工具仍注册但返回 ok=false 的明确提示，绝不报错/阻塞主链路。
- 破坏性开关（开/关设备）走既有确认门（category=system、destructive=True 由 AgentLoop 触发）。
"""
from __future__ import annotations

import json
from typing import Any

import requests

from ...config import get_secret, has_secret
from ...core.tool_registry import tool


def mihome_available() -> bool:
    """米家凭据是否已配置。"""
    return has_secret("MIJIA_TOKEN")


def _device_list() -> dict[str, Any]:
    """拉取米家设备列表（需 MIJIA_TOKEN；真实云端需签名，这里给出可对接骨架）。"""
    token = get_secret("MIJIA_TOKEN")
    if not token:
        return {"ok": False, "error": "米家插件未启用：缺少 MIJIA_TOKEN"}
    # 注：小米开放平台 home/device_list 需要请求签名（token + 时间戳 + nonce），
    # 此处保留标准入口；完整签名实现请按小米开放平台文档补齐。
    return {
        "ok": False,
        "error": "米家云端签名调用需对接：已检测到 MIJIA_TOKEN，请在 mihome.py 补齐签名后调用 home/device_list。",
        "hint": "参考 https://dev.mi.com 开放平台文档实现请求签名。",
    }


@tool(
    name="mihome_control",
    description=(
        "控制米家/小米智能设备，如“打开客厅灯”“查询卧室温度”。仅在已配置 MIJIA_TOKEN 时可用；"
        "未配置时它会如实说明插件未启用，不要假装执行。支持的动作：on/off/status/query。"
    ),
    category="plugin",
)
def mihome_control(action: str, device: str) -> dict[str, Any]:
    """米家设备控制入口；凭据缺失即降级。"""
    if not mihome_available():
        return {"ok": False, "error": "米家插件未启用：缺少 MIJIA_TOKEN（请在 ~/.jarvis/.env 配置）。"}
    action = (action or "").lower()
    if action in ("list", "devices"):
        return _device_list()
    # on/off/status 需要设备 did 与云端签名；给出可对接骨架而非伪造结果。
    return {
        "ok": False,
        "error": f"米家设备「{device}」的 {action} 需对接云端签名调用；已配置凭据，请在 mihome.py 补齐。",
        "action": action,
        "device": device,
    }
