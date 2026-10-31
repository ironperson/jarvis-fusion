"""多服务商选择器：N 路切换 + 可用性 / 激活状态展示（T13 / P0-04）。

交互要求（对齐 arch 类图 ProviderSelector）：
- 提供「默认 / 自动」与 6 家服务商切换；
- 切换即时生效（调用 EngineRouter.select）；
- 只有可用（已配置密钥 / 本地 Ollama）的服务商才高亮；不可用的保留显示但允许尝试（云端不可达会降级）。
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget


class ProviderSelector(QWidget):
    """N 路大脑服务商下拉选择器，切换时发射 sig_provider(provider_id)。"""

    sig_provider = Signal(str)

    def __init__(self, router: Any, parent: object | None = None) -> None:
        super().__init__(parent)
        self._router = router
        self.label = QLabel("大脑")
        self.combo = QComboBox()
        self.combo.setMinimumWidth(160)
        self.combo.currentIndexChanged.connect(self._on_pick)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.combo, 1)
        self.refresh()

    def refresh(self) -> None:
        """重建下拉并重设当前激活项为选中的引擎。"""
        self.combo.blockSignals(True)
        self.combo.clear()
        self._ids: list[str] = []
        active_id = ""
        for engine in self._router.list_engines():
            eid = str(engine["id"])
            name = str(engine["name"])
            mark = "✓" if engine["available"] else "—"
            active = "●" if engine["active"] else " "
            self.combo.addItem(f"{active} {name} {mark}", eid)
            self._ids.append(eid)
            if engine["active"]:
                active_id = eid
        if active_id:
            self.combo.setCurrentIndex(self._ids.index(active_id))
        self.combo.blockSignals(False)

    def _on_pick(self, index: int) -> None:
        if index < 0:
            return
        eid = str(self.combo.itemData(index) or "")
        if not eid:
            return
        if self._router.select(eid):
            self.sig_provider.emit(eid)
        else:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(self, "切换失败", f"未找到服务商：{eid}")
        self.refresh()
