"""工具调用过程树。"""
from __future__ import annotations

import json
from typing import Any

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem


class TraceView(QTreeWidget):
    """展示工具名、入参、返回、耗时和失败全文。"""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self.setHeaderLabels(["状态 / 工具", "耗时"])
        self.setColumnWidth(0, 390)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(False)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._task_counter = 0

    def begin_task(self, text: str) -> None:
        """开始新任务并清空旧过程。"""
        self.clear()
        self._items.clear()
        self._task_counter += 1
        root = QTreeWidgetItem([f"任务 #{self._task_counter}  {text[:42]}", "运行中"])
        root.setExpanded(True)
        font = QFont()
        font.setBold(True)
        root.setFont(0, font)
        self.addTopLevelItem(root)

    def on_tool_started(self, payload: dict[str, Any]) -> None:
        """新增运行中行并展开参数。"""
        call_id = str(payload.get("id") or "")
        name = str(payload.get("name") or "未知工具")
        item = QTreeWidgetItem([f"⟳  {name}", "运行中"])
        arguments = json.dumps(payload.get("arguments", {}), ensure_ascii=False, indent=2, default=str)
        argument_item = QTreeWidgetItem([f"入参\n{arguments[:2048]}", ""])
        item.addChild(argument_item)
        root = self.topLevelItem(0)
        if root is None:
            self.addTopLevelItem(item)
        else:
            root.addChild(item)
        item.setExpanded(True)
        self._items[call_id] = item

    def on_tool_finished(self, payload: dict[str, Any]) -> None:
        """把运行中行更新为成功、失败或用户取消。"""
        call_id = str(payload.get("tool_call_id") or "")
        item = self._items.get(call_id)
        if item is None:
            item = QTreeWidgetItem([str(payload.get("name") or "未知工具"), ""])
            root = self.topLevelItem(0)
            (root.addChild(item) if root is not None else self.addTopLevelItem(item))
        ok = bool(payload.get("ok"))
        cancelled = bool(payload.get("cancelled"))
        icon = "✓" if ok else ("⏸" if cancelled else "✗")
        name = str(payload.get("name") or "未知工具")
        elapsed = float(payload.get("elapsed_ms", 0.0) or 0.0)
        item.setText(0, f"{icon}  {name}")
        item.setText(1, f"{elapsed / 1000:.2f}s")
        output = str(payload.get("text") or payload.get("error") or "无返回内容")
        child = QTreeWidgetItem([f"返回\n{output[:2048]}", ""])
        item.addChild(child)
        if not ok:
            red = QColor("#ff6b6b")
            item.setForeground(0, red)
            child.setForeground(0, red)
        item.setExpanded(not ok)

    def finish_task(self, ok: bool) -> None:
        """更新根任务状态。"""
        root = self.topLevelItem(0)
        if root is not None:
            root.setText(1, "完成" if ok else "未完成")
