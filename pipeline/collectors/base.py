"""采集器基类与原始条目模型。

RawItem 是「采集」与「入库」之间的契约：采集器只负责把网页/视频/帖子
变成 RawItem（url + 标题 + 正文 + 来源），后续的整理、摘要、入库由
core/pipeline.py 统一编排。这样各来源采集器可独立替换、并发运行。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class RawItem:
    url: str
    title: str = ""
    content: str = ""
    source: str = "web"
    keywords: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)  # 关键帧/音频等本地文件路径
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def ok(self) -> bool:
        return bool(self.content and self.content.strip())


class BaseCollector(ABC):
    source_name: str = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}

    @abstractmethod
    def collect(self) -> list[RawItem]:
        """返回本次采集到的原始条目列表。"""
        raise NotImplementedError
