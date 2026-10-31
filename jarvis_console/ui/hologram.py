"""全息 3D 演示控制器（Python 侧）：枚举 HUD 演示并触发切换。

- 演示清单来自 hud/media/<id>/manifest.json，与前端 hud/demos/<id>.js 注册名对齐。
- 切换只向 HUD 桥发送 {state:"_demo", demo:<id>}，由 state.js 调用 JarvisDemos.load。
- 演示脚本懒加载：缺失不影响主循环；离线需 hud/vendor/three.min.js（打包时下载）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger
from ..paths import PACKAGE_ROOT

log = get_logger(__name__)

MEDIA_DIR = PACKAGE_ROOT / "hud" / "media"


class HologramDemoController:
    """读取演示清单并向前端下发切换指令。"""

    def __init__(self, push_fn: Any | None = None) -> None:
        """push_fn(state_dict)：实际推送 HUDState 的回调（默认无动作）。"""
        self._push = push_fn
        self._demos: dict[str, dict[str, Any]] = {}
        self._current: str | None = None
        self._scan()

    def _scan(self) -> None:
        """扫描 hud/media/*/manifest.json，构建 id→manifest 映射。"""
        if not MEDIA_DIR.is_dir():
            log.warning("演示媒体目录缺失：%s", MEDIA_DIR)
            return
        for manifest in MEDIA_DIR.glob("*/manifest.json"):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                log.warning("演示清单解析失败 %s：%s", manifest, exc)
                continue
            demo_id = str(data.get("id") or manifest.parent.name)
            data["id"] = demo_id
            self._demos[demo_id] = data
        log.info("已加载 %d 个全息演示：%s", len(self._demos), ", ".join(sorted(self._demos)))

    def available(self) -> list[dict[str, Any]]:
        """返回全部可用演示的清单（含 id/label/description/keywords）。"""
        return [dict(value) for value in sorted(self._demos.values(), key=lambda d: str(d.get("id")))]

    def ids(self) -> list[str]:
        """返回全部演示 id。"""
        return sorted(self._demos)

    def is_available(self, demo_id: str) -> bool:
        return demo_id in self._demos

    def current(self) -> str | None:
        return self._current

    def show(self, demo_id: str | None) -> bool:
        """切换 3D 演示；demo_id=None 表示关闭演示。成功返回 True。"""
        if demo_id is not None and demo_id not in self._demos:
            log.warning("未知演示 id：%s", demo_id)
            return False
        self._current = demo_id
        if self._push is not None:
            try:
                self._push({"state": "_demo", "demo": demo_id})
            except Exception as exc:  # noqa: BLE001
                log.debug("演示切换推送失败：%s", exc)
        return True

    def cycle(self) -> str | None:
        """在可用演示间循环切换；无可用演示时返回 None。"""
        ids = self.ids()
        if not ids:
            return None
        if self._current is None or self._current not in ids:
            nxt = ids[0]
        else:
            idx = ids.index(self._current)
            nxt = ids[(idx + 1) % len(ids)]
        self.show(nxt)
        return nxt

    def hide(self) -> None:
        """关闭当前演示。"""
        self.show(None)


def match_demo_by_keyword(text: str, controller: HologramDemoController) -> str | None:
    """依据中文关键词从用户输入中解析想要展示的演示；无匹配返回 None。"""
    lowered = (text or "").lower()
    for demo in controller.available():
        keywords = [str(k).lower() for k in demo.get("keywords", [])]
        if demo.get("id", "") in lowered or demo.get("label", "") in text:
            return str(demo.get("id"))
        for kw in keywords:
            if kw and kw in lowered:
                return str(demo.get("id"))
    return None
