"""新建 / 查看 / 取消提醒的 UI。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.reminders import get_scheduler


class ReminderDialog(QDialog):
    """日程提醒管理：新增、查看、取消。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("日程提醒")
        self.resize(460, 360)
        self._scheduler = get_scheduler()

        layout = QVBoxLayout(self)
        # 新增区
        add_layout = QHBoxLayout()
        self.time_edit = QLineEdit()
        self.time_edit.setPlaceholderText("时间，如 30分钟后 / 今天21:30 / 每天08:00")
        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("提醒内容，如 开会")
        add_button = QPushButton("添加")
        add_button.clicked.connect(self._add)
        add_layout.addWidget(QLabel("时间"))
        add_layout.addWidget(self.time_edit, 2)
        add_layout.addWidget(QLabel("内容"))
        add_layout.addWidget(self.text_edit, 2)
        add_layout.addWidget(add_button)
        layout.addLayout(add_layout)

        # 列表区
        layout.addWidget(QLabel("待办提醒"))
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)
        cancel_button = QPushButton("取消选中提醒")
        cancel_button.clicked.connect(self._cancel)
        layout.addWidget(cancel_button)

        self._refresh()
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def _add(self) -> None:
        when = self.time_edit.text().strip()
        text = self.text_edit.text().strip()
        if not when or not text:
            return
        result = self._scheduler.add(text, when)
        if result.get("ok"):
            self.time_edit.clear()
            self.text_edit.clear()
            self._refresh()

    def _cancel(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        reminder_id = item.data(0)
        self._scheduler.cancel(reminder_id)
        self._refresh()

    def _refresh(self) -> None:
        self.list_widget.clear()
        for entry in self._scheduler.list():
            human = entry["when_iso"].replace("T", " ")
            repeat = "（每天）" if entry.get("repeat") else ""
            label = f"[{entry['id']}] {human}{repeat}：{entry['text']}"
            list_item = QListWidgetItem(label)
            list_item.setData(Qt.ItemDataRole.UserRole, entry["id"])
            self.list_widget.addItem(list_item)
