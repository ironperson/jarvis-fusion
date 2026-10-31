"""首批 12 个工具 + 插件的集中导入与配置入口。"""
from __future__ import annotations

from ..config import get_config
from ..core.tool_registry import REGISTRY
from . import bilibili, fetchpage, filetools, knowledge, systemtools, websearch
from . import plugins  # 触发插件 @tool 注册（米家/高德/抖音/日程）

__all__ = ["REGISTRY", "configure_tools"]


def configure_tools() -> None:
    """按 console.yaml 的白名单裁剪显式注册表，再按凭据启用/禁用插件。"""
    config = get_config()
    REGISTRY.configure(config.tools_enabled, config.tools_disabled)
    plugins.sync_plugin_enabled()


configure_tools()
