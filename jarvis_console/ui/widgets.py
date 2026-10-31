"""引擎徽标、开关、记忆灯与沉淀提示条。"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QWidget


class EngineBadges(QWidget):
    """实时显示 DeepSeek/Ollama/Mock 活跃状态。"""

    def __init__(self, deepseek_configured: bool, parent: object | None = None) -> None:
        super().__init__(parent)
        self.deepseek = QLabel("DeepSeek 已配置" if deepseek_configured else "DeepSeek 未配置")
        self.ollama = QLabel("Ollama 待命")
        self.mock = QLabel("")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.deepseek)
        layout.addWidget(self.ollama)
        layout.addWidget(self.mock)

    def set_active(self, engine: str, cause: str = "") -> None:
        normalized = engine.lower()
        self.deepseek.setProperty("active", "deepseek" in normalized)
        self.ollama.setProperty("active", "ollama" in normalized)
        self.mock.setText("Mock 离线" if "mock" in normalized else "")
        self.setToolTip(cause)
        for label in (self.deepseek, self.ollama, self.mock):
            label.style().unpolish(label)
            label.style().polish(label)


class ControlPanel(QWidget):
    """播报、信任模式、记忆指示与手动授权入口。"""

    sig_authorize_requested = Signal()

    def __init__(self, speech_enabled: bool = True, parent: object | None = None) -> None:
        super().__init__(parent)
        self.speech = QCheckBox("播报")
        self.speech.setChecked(speech_enabled)
        self.trust = QCheckBox("信任模式")
        self.trust.setChecked(False)
        self.trust.setToolTip("默认关闭；开启后敏感目录仍会二次确认，且绝不绕过写入白名单。")
        self.authorize = QPushButton("授权目录")
        self.authorize.setToolTip("手动选择一个路径，允许本次运行向其写入。")
        self.authorize.clicked.connect(self.sig_authorize_requested.emit)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.speech)
        layout.addWidget(self.trust)
        layout.addWidget(self.authorize)


class MemoryIndicator(QLabel):
    """悬停显示本轮召回的长期记忆条目。"""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__("记忆 0 条", parent)
        self.set_hits([])

    def set_hits(self, hits: list[dict[str, Any]]) -> None:
        self.setText(f"记忆 {len(hits)} 条")
        if hits:
            self.setToolTip("\n".join(
                f"{item.get('key', '')}：{item.get('value', '')}" for item in hits
            ))
            self.setProperty("active", True)
        else:
            self.setToolTip("本轮没有召回长期记忆")
            self.setProperty("active", False)
        self.style().unpolish(self)
        self.style().polish(self)


class IngestBanner(QWidget):
    """明示知识库沉淀，并提供当条/会话内撤回入口。"""

    sig_revoke = Signal(list)

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._note_ids: list[str] = []
        self.label = QLabel("")
        self.button = QPushButton("撤回本条沉淀")
        self.button.clicked.connect(self._emit_revoke)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.button)
        self.setVisible(False)

    def show_notes(self, note_ids: list[str]) -> None:
        self._note_ids = [item for item in note_ids if item]
        self.label.setText(
            f"已沉淀进知识库：{len(self._note_ids)} 条（note_id: {', '.join(self._note_ids)}）"
        )
        self.setVisible(bool(self._note_ids))

    def _emit_revoke(self) -> None:
        if self._note_ids:
            self.sig_revoke.emit(list(self._note_ids))

    def mark_revoked(self, succeeded: list[str]) -> None:
        succeeded_set = set(succeeded)
        self._note_ids = [item for item in self._note_ids if item not in succeeded_set]
        if self._note_ids:
            self.label.setText(f"尚未撤回：{', '.join(self._note_ids)}")
        else:
            self.label.setText("本条沉淀已撤回")
            self.button.setEnabled(False)
