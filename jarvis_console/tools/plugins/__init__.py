"""插件聚合导入 + 凭据驱动启用。"""
from __future__ import annotations

# 顺序导入各插件模块，触发 @tool 注册；注册发生在导入时。
from . import amap, douyin_creator, mihome, schedule, sync

__all__ = ["amap", "douyin_creator", "mihome", "schedule", "sync"]

# 对外暴露便捷函数。
sync_plugin_enabled = sync.sync_plugin_enabled
plugin_status = sync.plugin_status
