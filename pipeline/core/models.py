"""知识库数据模型（PKM 视角）。

四步法第 2 步「建模」：每张 Note 是一个原子笔记（乐高式，只装一个想法）。
- note_id：唯一 ID（用于双向链接与检索定位）
- note_type：Zettelkasten 三类（临时/文献/永久）
- para：PARA 四层分类（决定这条笔记该待在哪）
- tags / links：标签与双向链接（连接性，MOC 索引预留）
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum


class NoteType(str, Enum):
    FLEETING = "fleeting"      # 临时笔记：一闪而过的灵感
    LITERATURE = "literature"  # 文献笔记：书/文/视频摘要
    PERMANENT = "permanent"    # 永久笔记：原子化独立想法


class ParaCategory(str, Enum):
    PROJECT = "project"    # 有截止日期
    AREA = "area"          # 持续负责的领域
    RESOURCE = "resource"  # 感兴趣的参考
    ARCHIVE = "archive"    # 不再活跃


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Note:
    note_id: str
    title: str
    content: str
    source: str = "manual"            # web | xiaohongshu | douyin | manual
    url: str = ""
    note_type: NoteType = NoteType.LITERATURE
    para: ParaCategory = ParaCategory.RESOURCE
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)  # 关联 note_id（双向链接预留）
    summary: str = ""
    attachments: list[str] = field(default_factory=list)  # 关键帧/音频等本地文件路径（多模态落点）
    created_at: str = field(default_factory=_now_iso)
    raw_path: str = ""
    embedding_id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["note_type"] = self.note_type.value
        d["para"] = self.para.value
        return d
