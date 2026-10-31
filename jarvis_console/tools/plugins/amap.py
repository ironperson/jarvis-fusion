"""高德地图插件：天气 + 路线（凭据缺失即禁用，R2）。

高德开放平台 Web 服务 API，仅需 ``AMAP_KEY``；缺失即返回 ok=false 的明确提示。
"""
from __future__ import annotations

from typing import Any

import requests

from ...config import get_secret, has_secret
from ...core.tool_registry import tool

AMAP_BASE = "https://restapi.amap.com"
_TIMEOUT = 8


def amap_available() -> bool:
    """高德凭据是否已配置。"""
    return has_secret("AMAP_KEY")


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    key = get_secret("AMAP_KEY")
    params = dict(params)
    params["key"] = key
    try:
        response = requests.get(f"{AMAP_BASE}{path}", params=params, timeout=_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": f"高德请求失败：{exc}"}
    if str(data.get("status")) != "1":
        return {"ok": False, "error": f"高德返回错误：{data.get('info', '未知')}"}
    return {"ok": True, "data": data, "source": "高德开放平台"}


@tool(
    name="amap_weather",
    description=(
        "查询城市实时天气与预警（高德）。仅在已配置 AMAP_KEY 时可用；未配置会如实说明未启用。"
        "参数 city 为城市名或 adcode，如“北京”。"
    ),
    category="plugin",
)
def amap_weather(city: str) -> dict[str, Any]:
    """高德天气查询。"""
    if not amap_available():
        return {"ok": False, "error": "高德插件未启用：缺少 AMAP_KEY（请在 ~/.jarvis/.env 配置）。"}
    result = _get("/v3/weather/weatherInfo", {"city": city, "extensions": "base", "output": "JSON"})
    if not result.get("ok"):
        return result
    lives = (result["data"].get("lives") or [{}])[0]
    text = (
        f"{lives.get('city', city)}：{lives.get('weather', '')}，"
        f"温度 {lives.get('temperature', '')}℃，"
        f"风向 {lives.get('winddirection', '')}{lives.get('windpower', '')}级，"
        f"湿度 {lives.get('humidity', '')}%，发布时间 {lives.get('reporttime', '')}。"
        f"（来源：高德开放平台）"
    )
    return {"ok": True, "text": text, "data": lives, "source": "高德开放平台"}


@tool(
    name="amap_route",
    description=(
        "规划从起点到终点的驾车/公交/步行路线（高德）。仅在已配置 AMAP_KEY 时可用。"
        "origin/destination 为“经度,纬度”或城市地址；mode 可为 driving/transit/walking。"
    ),
    category="plugin",
)
def amap_route(origin: str, destination: str, mode: str = "driving") -> dict[str, Any]:
    """高德路径规划。"""
    if not amap_available():
        return {"ok": False, "error": "高德插件未启用：缺少 AMAP_KEY（请在 ~/.jarvis/.env 配置）。"}
    path = {
        "driving": "/v3/direction/driving",
        "transit": "/v3/direction/transit",
        "walking": "/v3/direction/walking",
    }.get(mode, "/v3/direction/driving")
    result = _get(path, {"origin": origin, "destination": destination})
    if not result.get("ok"):
        return result
    route = result["data"].get("route") or {}
    steps: list[str] = []
    for step in route.get("paths") or []:
        for seg in step.get("steps") or []:
            instruction = seg.get("instruction") or seg.get("road_name") or ""
            if instruction:
                steps.append(instruction)
    summary = route.get("taxi_cost") or route.get("distance") or ""
    text = f"从 {origin} 到 {destination}（{mode}）：约 {summary}。" + (
        " 路线：" + " ".join(steps[:6]) if steps else ""
    )
    return {"ok": True, "text": text, "data": route, "source": "高德开放平台"}
