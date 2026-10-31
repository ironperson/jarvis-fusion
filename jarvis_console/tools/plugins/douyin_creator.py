"""抖音创作者开放接口插件（替代旧 stub，R2：凭据缺失即禁用）。

走抖音开放平台 creator 接口（open.douyin.com），仅需 ``DOUYIN_ACCESS_TOKEN``。
缺失即返回 ok=false 的明确提示，不报错、不阻塞主链路。
"""
from __future__ import annotations

from typing import Any

import requests

from ...config import get_secret, has_secret
from ...core.tool_registry import tool

DOUYIN_BASE = "https://open.douyin.com"
_TIMEOUT = 8


def douyin_available() -> bool:
    """抖音开放平台凭据是否已配置。"""
    return has_secret("DOUYIN_ACCESS_TOKEN")


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    token = get_secret("DOUYIN_ACCESS_TOKEN")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(f"{DOUYIN_BASE}{path}", params=params, headers=headers, timeout=_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": f"抖音请求失败：{exc}"}
    if str(data.get("status_code", data.get("code", 0))) not in ("0", 0, "ok"):
        return {"ok": False, "error": f"抖音返回错误：{data.get('description', data.get('message', '未知'))}"}
    return {"ok": True, "data": data.get("data", data), "source": "抖音开放平台"}


@tool(
    name="douyin_creator_stats",
    description=(
        "查询抖音创作者数据（作品/播放/涨粉等）。仅在已配置 DOUYIN_ACCESS_TOKEN 时可用；"
        "未配置会如实说明未启用。注意：本工具只走创作者开放接口，不做本地视频 ASR。"
    ),
    category="plugin",
)
def douyin_creator_stats(metric: str = "overview") -> dict[str, Any]:
    """抖音创作者数据查询；凭据缺失即降级。"""
    if not douyin_available():
        return {
            "ok": False,
            "error": "抖音创作者插件未启用：缺少 DOUYIN_ACCESS_TOKEN（请在 ~/.jarvis/.env 配置）。",
        }
    # creator 开放接口：/video/list/ 取作品；/data/external/... 取各项指数（需对应权限）。
    if metric in ("videos", "works"):
        result = _get("/video/list/", {"count": 10})
        if not result.get("ok"):
            return result
        videos = result["data"].get("list", []) if isinstance(result["data"], dict) else []
        summary = "；".join(str(v.get("desc", ""))[:20] for v in (videos or [])[:5])
        return {"ok": True, "text": f"近 5 条作品：{summary}", "data": videos, "source": "抖音开放平台"}
    return {
        "ok": True,
        "text": "抖音创作者概览接口需按开放平台权限开通 data/external 指标；已配置凭据，请按需扩展。",
        "metric": metric,
        "source": "抖音开放平台",
    }
