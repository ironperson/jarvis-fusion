"""B站官方视频搜索：WBI 签名、buvid3 与离线夹具。"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from ..core.tool_registry import tool
from ..paths import FIXTURES_DIR

_API_BASE = "https://api.bilibili.com"
_MIXIN_TABLE = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)
_ALLOWED_ORDERS = {"click", "pubdate", "dm", "stow", "totalrank"}
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _mock_enabled() -> bool:
    return os.environ.get("JARVIS_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}


def _mixin_key(img_key: str, sub_key: str) -> str:
    source = img_key + sub_key
    return "".join(source[index] for index in _MIXIN_TABLE if index < len(source))[:32]


def _wbi_sign(params: dict[str, Any], img_key: str, sub_key: str) -> dict[str, Any]:
    """按 B站 WBI 规则排序、过滤并生成 w_rid。"""
    signed = dict(params)
    signed["wts"] = int(time.time())
    safe: dict[str, str] = {}
    for key in sorted(signed):
        value = re.sub(r"[!'()*]", "", str(signed[key]))
        safe[key] = value
    query = urlencode(safe)
    safe["w_rid"] = hashlib.md5(f"{query}{_mixin_key(img_key, sub_key)}".encode("utf-8")).hexdigest()
    return safe


def _key_from_url(url: str) -> str:
    return Path(url.split("?", 1)[0]).stem


def _normalize_results(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    raw_results = payload.get("results")
    if raw_results is None:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        raw_results = data.get("result", [])
    if not isinstance(raw_results, list):
        return []
    results: list[dict[str, Any]] = []
    for raw in raw_results[:limit]:
        if not isinstance(raw, dict):
            continue
        title = re.sub(r"<[^>]+>", "", html.unescape(str(raw.get("title") or ""))).strip()
        bvid = str(raw.get("bvid") or "")
        url = str(raw.get("arcurl") or (f"https://www.bilibili.com/video/{bvid}" if bvid else ""))
        play_value = raw.get("play", 0)
        try:
            play = int(str(play_value).replace(",", ""))
        except ValueError:
            play = 0
        results.append({
            "title": title,
            "author": str(raw.get("author") or raw.get("up_name") or ""),
            "play": play,
            "bvid": bvid,
            "url": url,
            "duration": str(raw.get("duration") or ""),
            "pubdate": int(raw.get("pubdate", 0) or 0),
            "pic": str(raw.get("pic") or ""),
        })
    return results


def _request_json(session: requests.Session, path: str, **kwargs: Any) -> dict[str, Any]:
    response = session.get(f"{_API_BASE}{path}", timeout=15, **kwargs)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("B站接口响应不是 JSON 对象")
    return payload


@tool(
    name="search_bilibili",
    description=(
        "按关键词搜索 B站公开视频并取得真实播放量。需要按播放量、最新、弹幕或收藏筛视频时使用。"
        "它不是抖音搜索；用户提抖音时必须先说明本版没开抖音站内搜索，再用本工具和 web_search。"
    ),
    category="collect",
)
def search_bilibili(keyword: str, order: str = "click", limit: int = 10) -> dict[str, Any]:
    """返回 B站视频标题、UP主、播放量、BV号与链接。"""
    clean_keyword = keyword.strip()
    if not clean_keyword:
        return {"ok": False, "reason": "搜索关键词不能为空", "results": []}
    normalized_order = order if order in _ALLOWED_ORDERS else "click"
    bounded_limit = min(20, max(1, int(limit)))
    if _mock_enabled():
        payload = json.loads((FIXTURES_DIR / "bilibili_search_sample.json").read_text(encoding="utf-8"))
        results = _normalize_results(payload, bounded_limit)
        return {
            "ok": True,
            "source": "bilibili_fixture",
            "keyword": clean_keyword,
            "order": normalized_order,
            "count": len(results),
            "results": results,
            "text": f"离线夹具检索到 {len(results)} 条 B站视频，含真实样本播放量字段。",
        }
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT, "Referer": "https://www.bilibili.com/"})
    base_params: dict[str, Any] = {
        "search_type": "video",
        "keyword": clean_keyword,
        "order": normalized_order,
        "page": 1,
        "page_size": bounded_limit,
    }
    errors: list[str] = []
    try:
        spi = _request_json(session, "/x/frontend/finger/spi")
        buvid = str((spi.get("data") or {}).get("b3") or "")
        if buvid:
            session.cookies.set("buvid3", buvid, domain=".bilibili.com")
        navigation = _request_json(session, "/x/web-interface/nav")
        wbi = (navigation.get("data") or {}).get("wbi_img") or {}
        img_key = _key_from_url(str(wbi.get("img_url") or ""))
        sub_key = _key_from_url(str(wbi.get("sub_url") or ""))
        if not img_key or not sub_key:
            raise ValueError("nav 未返回 WBI 图片密钥")
        signed_params = _wbi_sign(base_params, img_key, sub_key)
        payload = _request_json(
            session,
            "/x/web-interface/wbi/search/type",
            params=signed_params,
        )
        results = _normalize_results(payload, bounded_limit)
        if results:
            return {
                "ok": True,
                "source": "bilibili",
                "keyword": clean_keyword,
                "order": normalized_order,
                "count": len(results),
                "results": results,
                "text": f"B站官方搜索返回 {len(results)} 条视频。",
            }
        errors.append(f"WBI 接口无数据（code={payload.get('code')}）")
    except Exception as exc:
        errors.append(f"WBI 签名请求失败：{type(exc).__name__} {exc}")
    try:
        payload = _request_json(
            session,
            "/x/web-interface/search/type",
            params=base_params,
        )
        results = _normalize_results(payload, bounded_limit)
        if results:
            return {
                "ok": True,
                "source": "bilibili_unsigned_fallback",
                "keyword": clean_keyword,
                "order": normalized_order,
                "count": len(results),
                "results": results,
                "warning": "WBI 签名路径失败，已使用未签名降级接口。",
                "text": f"B站降级搜索返回 {len(results)} 条视频。",
            }
        errors.append(f"未签名接口无数据（code={payload.get('code')}）")
    except Exception as exc:
        errors.append(f"未签名请求失败：{type(exc).__name__} {exc}")
    return {
        "ok": False,
        "reason": "B站接口返回 412/无数据或网络不可达；请如实说明并改走 web_search。" + "；".join(errors),
        "results": [],
    }
