"""MyKnowledge 检索与入库工具。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core.notestore import NoteStore, mock_notestore
from ..core.tool_registry import tool
from ..paths import HOME_JARVIS

_STORE_CACHE: dict[str, NoteStore] = {}


def _store() -> NoteStore:
    mock = os.environ.get("JARVIS_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}
    if mock:
        root = Path(os.environ.get("JARVIS_MOCK_DIR", str(HOME_JARVIS / "mock_workspace"))).resolve()
        key = f"mock:{root}"
        if key not in _STORE_CACHE:
            _STORE_CACHE[key] = mock_notestore(root / "knowledge")
        return _STORE_CACHE[key]
    if "production" not in _STORE_CACHE:
        _STORE_CACHE["production"] = NoteStore()
    return _STORE_CACHE["production"]


@tool(
    name="knowledge_search",
    description=(
        "语义检索 MyKnowledge。用户问“之前存过什么”、需要引用旧资料或整理既有笔记时使用。"
        "回答必须保留命中的 note_id、相似度和 raw_path；不要把低相似度结果说成确定事实。"
    ),
    category="knowledge",
)
def knowledge_search(query: str, top_k: int = 5) -> dict[str, Any]:
    """返回知识库命中的标题、摘要、相似度、ID 与原文路径。"""
    if not query.strip():
        return {"ok": False, "reason": "检索词不能为空", "results": []}
    try:
        rows = _store().search(query.strip(), top_k=min(20, max(1, int(top_k))))
    except Exception as exc:
        return {"ok": False, "reason": f"知识库检索失败：{type(exc).__name__} {exc}", "results": []}
    results: list[dict[str, Any]] = []
    for row in rows:
        content = str(row.get("content") or "")
        results.append({
            "title": str(row.get("title") or ""),
            "summary": str(row.get("summary") or content[:300]),
            "score": float(row.get("score", 0.0) or 0.0),
            "note_id": str(row.get("note_id") or ""),
            "raw_path": str(row.get("raw_path") or ""),
            "source": str(row.get("source") or ""),
            "url": str(row.get("url") or ""),
        })
    return {
        "ok": True,
        "count": len(results),
        "results": results,
        "text": f"知识库命中 {len(results)} 条记录。",
    }


@tool(
    name="ingest_note",
    description=(
        "把有长期参考价值的清单、结论、方法论或资料索引沉淀为 MyKnowledge 原子笔记。"
        "闲聊、临时计算、一次性查询不要入库。成功后 UI 会明示 note_id 并提供撤回。"
    ),
    category="knowledge",
)
def ingest_note(
    title: str,
    content: str,
    source: str = "jarvis_console",
    tags: list[str] | None = None,
    url: str = "",
) -> dict[str, Any]:
    """每天首写先备份，随后调用既有 pipeline.ingest.ingest_note。"""
    return _store().ingest(title, content, source, list(tags or []), url)
