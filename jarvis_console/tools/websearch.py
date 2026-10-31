"""零 Key 网页搜索：DuckDuckGo HTML → Bing HTML 降级。"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests

from ..core.errors import DependencyMissing
from ..core.tool_registry import tool
from ..paths import FIXTURES_DIR

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(slots=True)
class SearchItem:
    """搜索结果标准结构。"""

    title: str
    url: str
    snippet: str

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class TavilyProvider:
    """付费搜索接口预留位；本版明确不接 Key、不发请求。"""

    def search(self, query: str, limit: int) -> list[SearchItem]:
        del query, limit
        raise DependencyMissing("本版未启用 Tavily；请使用零 Key 的 DuckDuckGo/Bing HTML 搜索。")


def _clean_markup(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()


def _ddg_url(raw_url: str) -> str:
    value = html.unescape(raw_url)
    parsed = urlparse(value)
    if parsed.path.startswith("/l/"):
        redirect = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(redirect) if redirect else value
    return value


def _parse_ddg(document: str, limit: int) -> list[SearchItem]:
    anchor_pattern = re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    snippets = re.findall(
        r'<(?:a|div)[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>',
        document,
        flags=re.IGNORECASE | re.DOTALL,
    )
    items: list[SearchItem] = []
    for index, (url, title) in enumerate(anchor_pattern.findall(document)):
        clean_url = _ddg_url(url)
        if not clean_url.startswith(("http://", "https://")):
            continue
        snippet = _clean_markup(snippets[index]) if index < len(snippets) else ""
        items.append(SearchItem(_clean_markup(title), clean_url, snippet))
        if len(items) >= limit:
            break
    return items


def _parse_bing(document: str, limit: int) -> list[SearchItem]:
    blocks = re.findall(
        r'<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>',
        document,
        flags=re.IGNORECASE | re.DOTALL,
    )
    items: list[SearchItem] = []
    for block in blocks:
        anchor = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.I | re.S)
        if anchor is None:
            continue
        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.I | re.S)
        items.append(SearchItem(
            _clean_markup(anchor.group(2)),
            html.unescape(anchor.group(1)),
            _clean_markup(snippet_match.group(1)) if snippet_match else "",
        ))
        if len(items) >= limit:
            break
    return items


def _fetch_html(url: str, params: dict[str, str]) -> str:
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=15,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


@tool(
    name="web_search",
    description=(
        "通用网页关键词搜索，零 API Key。B站以外的资料、补充来源或 B站接口失败时使用。"
        "结果来自 DuckDuckGo，失败自动尝试 Bing；两者都失败时必须如实说明，不得编造搜索结果。"
    ),
    category="collect",
)
def web_search(query: str, limit: int = 5) -> dict[str, Any]:
    """返回标题、URL 和摘要；网络失败时返回 ok=false 与原因。"""
    clean_query = query.strip()
    if not clean_query:
        return {"ok": False, "reason": "搜索关键词不能为空", "results": []}
    bounded_limit = min(10, max(1, int(limit)))
    if os.environ.get("JARVIS_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}:
        document = (FIXTURES_DIR / "websearch_sample.html").read_text(encoding="utf-8")
        items = _parse_ddg(document, bounded_limit)
        return {
            "ok": True,
            "provider": "fixture",
            "count": len(items),
            "results": [item.to_dict() for item in items],
            "text": f"离线夹具网页搜索返回 {len(items)} 条结果。",
        }
    failures: list[str] = []
    try:
        items = _parse_ddg(_fetch_html("https://html.duckduckgo.com/html/", {"q": clean_query}), bounded_limit)
        if items:
            return {
                "ok": True,
                "provider": "duckduckgo",
                "count": len(items),
                "results": [item.to_dict() for item in items],
                "text": f"DuckDuckGo 返回 {len(items)} 条网页结果。",
            }
        failures.append("DuckDuckGo 页面未解析到结果")
    except Exception as exc:
        failures.append(f"DuckDuckGo：{type(exc).__name__} {exc}")
    try:
        items = _parse_bing(_fetch_html("https://www.bing.com/search", {"q": clean_query}), bounded_limit)
        if items:
            return {
                "ok": True,
                "provider": "bing",
                "count": len(items),
                "results": [item.to_dict() for item in items],
                "text": f"Bing 降级搜索返回 {len(items)} 条网页结果。",
            }
        failures.append("Bing 页面未解析到结果")
    except Exception as exc:
        failures.append(f"Bing：{type(exc).__name__} {exc}")
    return {"ok": False, "reason": "；".join(failures), "results": []}
