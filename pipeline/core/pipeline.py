"""采集编排（四步法第 3 步「入流」的总调度）。

串联：采集器.collect() → 摘要(仅 DeepSeek，决策 A) → ingest_note() 入库 → 任务状态落库。

对应 Aivy OS 机制：
- 「子智能体并行」：各采集器内部已并发（见 collectors/web.py）。
- 「Mission 长任务」：每条 url 的采集结果写入 tasks 表（status/retries），跨天可续跑。

铁律（呼应「本地优先、云端仅摘要」）：摘要环节才允许调 DeepSeek（llm.summarize），
抽取/结构化一律不在此发生；若 DeepSeek 不可用，llm.summarize 内部已降级本地 qwen3-14b。
"""
from __future__ import annotations
import hashlib
import inspect
from pathlib import Path

import yaml

from core.db import init_db, upsert_task, upsert_note
from core.models import Note, NoteType, ParaCategory
from core.llm import summarize
from ingest.ingest import ingest_note
from collectors.base import BaseCollector, RawItem
from collectors.web import WebCollector
from collectors.xiaohongshu import XiaohongshuCollector
from collectors.douyin import DouyinCollector


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def load_settings() -> dict:
    return load_yaml(Path(__file__).resolve().parent.parent / "config" / "settings.yaml")


def load_sources() -> dict:
    return load_yaml(Path(__file__).resolve().parent.parent / "config" / "sources.yaml")


def _note_id_for(source: str, url: str) -> str:
    h = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
    return f"{source}-{h}"


def _build_collector(source_name: str, cfg: dict) -> BaseCollector | None:
    if source_name == "web":
        return WebCollector(cfg)
    if source_name == "xiaohongshu":
        return XiaohongshuCollector(cfg)
    if source_name == "douyin":
        return DouyinCollector(cfg)
    print(f"[pipeline] 来源 {source_name} 尚未实现，跳过。")
    return None


def run_source(source_name: str, do_summary: bool = True) -> dict:
    """对单个来源执行一次完整采集+入库。返回统计字典。"""
    settings = load_settings()
    sources = load_sources()
    scfg = sources.get(source_name, {})
    if not scfg.get("enabled", False):
        return {"source": source_name, "skipped": True, "reason": "未启用"}

    collector = _build_collector(source_name, scfg)
    if collector is None:
        return {"source": source_name, "skipped": True, "reason": "无可用采集器"}

    conn = init_db()
    print(f"[pipeline] 开始采集来源：{source_name}")

    raw: list[RawItem] = collector.collect()
    if inspect.iscoroutine(raw):
        import asyncio
        raw = asyncio.run(raw)

    stats = {"source": source_name, "collected": len(raw), "ingested": 0, "failed": 0}
    for item in raw:
        task_id = _note_id_for(source_name, item.url)
        try:
            if not item.ok:
                raise ValueError("正文为空（抓取/解析失败）")
            summary = summarize(item.content, settings) if do_summary else ""
            note = Note(
                note_id=task_id,
                title=item.title or item.url,
                content=item.content,
                source=source_name,
                url=item.url,
                note_type=NoteType.LITERATURE,
                para=ParaCategory.RESOURCE,
                tags=item.keywords or [source_name],
                attachments=item.attachments,
                summary=summary,
            )
            ingest_note(note, settings)
            upsert_task(conn, task_id, source_name, item.url, "done", 0)
            stats["ingested"] += 1
            print(f"  ✓ 入库：{note.title[:40]}  ({task_id})")
        except Exception as e:
            upsert_task(conn, task_id, source_name, item.url, "failed", 0, str(e)[:500])
            stats["failed"] += 1
            print(f"  ✗ 失败：{item.url} -> {e}")

    print(f"[pipeline] {source_name} 完成：入库 {stats['ingested']}，失败 {stats['failed']}。")
    return stats


def run_all_enabled(do_summary: bool = True) -> list[dict]:
    sources = load_sources()
    results = []
    for name in sources:
        if sources.get(name, {}).get("enabled"):
            results.append(run_source(name, do_summary=do_summary))
    return results
