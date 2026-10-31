"""采集器包。

对应 Aivy OS 的「子智能体后台并行」：每个来源是一个独立采集器，
可在调度层并发触发，互不阻塞。当前实现 web，xiaohongshu/douyin 留接口。
"""
from .base import BaseCollector, RawItem

__all__ = ["BaseCollector", "RawItem"]
