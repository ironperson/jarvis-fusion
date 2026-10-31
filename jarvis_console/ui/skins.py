"""八套皮肤（Python 侧）：与前端 hud/js/skins.js 的 SKINS 完全对齐。

- SkinRegistry 提供枚举、按 id 取值、按状态取核心色（供非 HUD 组件复用，如托盘/徽标）。
- SkinPicker 是下拉选择器，便于在 MainWindow 中切换皮肤并下发 HUD。
- 皮肤切换只向 HUD 桥发送 {state:"_skin", skin:<id>}，由 state.js 调 applySkin。
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget


# 与 hud/js/skins.js 的 SKINS 保持同步（id/label/core 四态色）。
SKINS: list[dict[str, Any]] = [
    {"id": "jarvis", "label": "经典贾维斯", "core": {"idle": "#3fa9ff", "listening": "#39e0d0", "thinking": "#9a6bff", "speaking": "#ffcf6b"}},
    {"id": "iron", "label": "钢铁红", "core": {"idle": "#ff5a5a", "listening": "#ff8a5a", "thinking": "#ff5ad0", "speaking": "#ffd15a"}},
    {"id": "ocean", "label": "深海蓝", "core": {"idle": "#2f6bff", "listening": "#2fd6ff", "thinking": "#3f9aff", "speaking": "#7affea"}},
    {"id": "matrix", "label": "矩阵绿", "core": {"idle": "#26ff8c", "listening": "#7dff5a", "thinking": "#26ffd0", "speaking": "#caff5a"}},
    {"id": "gold", "label": "黄金管家", "core": {"idle": "#caa64a", "listening": "#e0c060", "thinking": "#b07aff", "speaking": "#ffd15a"}},
    {"id": "rose", "label": "蔷薇粉", "core": {"idle": "#ff6fa0", "listening": "#ff9ad0", "thinking": "#c06bff", "speaking": "#ffc15a"}},
    {"id": "ice", "label": "寒冰银", "core": {"idle": "#9fd6ff", "listening": "#c8f0ff", "thinking": "#7fa8ff", "speaking": "#ffe0a0"}},
    {"id": "amber", "label": "琥珀橙", "core": {"idle": "#ff9a3f", "listening": "#ffc36b", "thinking": "#ff6fae", "speaking": "#ffe06b"}},
]

DEFAULT_SKIN = "jarvis"

# 四态常量（与 voice_controller 保持一致，避免循环依赖，此处仅作映射键）。
STATES = ("idle", "listening", "thinking", "speaking")


class SkinRegistry:
    """皮肤注册表：枚举、取值、按状态取色的纯数据服务。"""

    _by_id: dict[str, dict[str, Any]] = {item["id"]: item for item in SKINS}

    @classmethod
    def list(cls) -> list[dict[str, Any]]:
        return [dict(item) for item in SKINS]

    @classmethod
    def ids(cls) -> list[str]:
        return [item["id"] for item in SKINS]

    @classmethod
    def get(cls, skin_id: str) -> dict[str, Any]:
        return dict(cls._by_id.get(skin_id, cls._by_id[DEFAULT_SKIN]))

    @classmethod
    def color_for(cls, skin_id: str, state: str) -> str:
        skin = cls.get(skin_id)
        return str(skin["core"].get(state, skin["core"]["idle"]))

    @classmethod
    def label(cls, skin_id: str) -> str:
        return str(cls.get(skin_id).get("label", skin_id))


class SkinPicker(QWidget):
    """皮肤下拉选择器，切换时发射 sig_skin(skin_id)。"""

    sig_skin = Signal(str)

    def __init__(self, current: str = DEFAULT_SKIN, parent: object | None = None) -> None:
        super().__init__(parent)
        self.label = QLabel("皮肤")
        self.combo = QComboBox()
        for item in SKINS:
            self.combo.addItem(f"{item['label']}", item["id"])
        idx = self.combo.findData(current or DEFAULT_SKIN)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
        self.combo.currentIndexChanged.connect(self._on_changed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.combo)

    def _on_changed(self, index: int) -> None:
        skin_id = str(self.combo.itemData(index) or DEFAULT_SKIN)
        self.sig_skin.emit(skin_id)

    def current_skin(self) -> str:
        return str(self.combo.currentData() or DEFAULT_SKIN)
