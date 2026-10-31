"""直接调用 WebCollector 抓页，明确绕过 run_source enabled 门。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core.tool_registry import tool
from ..paths import FIXTURES_DIR


@tool(
    name="fetch_page",
    description=(
        "抓取一个已知 URL 的网页正文。仅在已有具体链接、需要读详情或引用原文时使用；"
        "寻找链接应先用 search_bilibili/web_search。单页失败要保留其他已成功结果。"
    ),
    category="collect",
)
def fetch_page(url: str, max_chars: int = 6000) -> dict[str, Any]:
    """绕过 pipeline.run_source，直接 WebCollector({seeds:[url]}).collect()。"""
    clean_url = url.strip()
    if not clean_url:
        return {"ok": False, "reason": "URL 不能为空", "url": clean_url}
    bounded_chars = min(50000, max(100, int(max_chars)))
    seed = clean_url
    if os.environ.get("JARVIS_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}:
        seed = str((FIXTURES_DIR / "page_sample.html").resolve())
    try:
        from collectors.web import WebCollector
    except ImportError as exc:
        return {"ok": False, "reason": f"网页采集依赖不可用：{exc}", "url": clean_url}
    collector = WebCollector({
        "seeds": [seed],
        "timeout": 20,
        "concurrency": 1,
        "verify_ssl": True,
        "discover_links": False,
    })
    items = collector.collect()
    if not items or not items[0].ok:
        return {"ok": False, "reason": "网页抓取或正文抽取失败", "url": clean_url}
    item = items[0]
    content = item.content.strip()
    truncated = len(content) > bounded_chars
    content = content[:bounded_chars]
    return {
        "ok": True,
        "url": clean_url,
        "title": item.title or Path(clean_url).name,
        "content": content,
        "keywords": list(item.keywords),
        "truncated": truncated,
        "text": f"已抓取《{item.title or clean_url}》正文 {len(content)} 字" + ("（已截断）" if truncated else ""),
    }
