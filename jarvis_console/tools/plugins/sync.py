"""插件凭据驱动启用：凭据缺失即禁用（R2），不报错、不阻塞主链路。"""
from __future__ import annotations

from typing import Any

from ...config import has_secret
from ...core.tool_registry import REGISTRY

# 插件工具 → 所需凭据键；全部齐备才启用，否则禁用。
PLUGIN_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "mihome_control": ("MIJIA_TOKEN",),
    "amap_weather": ("AMAP_KEY",),
    "amap_route": ("AMAP_KEY",),
    "douyin_creator_stats": ("DOUYIN_ACCESS_TOKEN",),
}


def sync_plugin_enabled() -> dict[str, bool]:
    """按凭据齐备情况设置插件工具的 enabled 标志，返回各插件最终状态。"""
    status: dict[str, bool] = {}
    for name, keys in PLUGIN_CREDENTIALS.items():
        spec = REGISTRY._tools.get(name)
        if spec is None:
            continue
        enabled = all(has_secret(key) for key in keys)
        spec.enabled = enabled
        status[name] = enabled
    return status


def plugin_status() -> dict[str, Any]:
    """返回每个插件的可用性与凭据配置情况（UI/日志只显示是否已配置，不回显值）。"""
    out: dict[str, Any] = {}
    for name, keys in PLUGIN_CREDENTIALS.items():
        spec = REGISTRY._tools.get(name)
        out[name] = {
            "registered": spec is not None,
            "enabled": spec.enabled if spec is not None else False,
            "credentials_configured": all(has_secret(key) for key in keys),
        }
    return out
