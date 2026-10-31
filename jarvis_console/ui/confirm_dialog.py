"""破坏性操作专用确认框。"""
from __future__ import annotations

import html
from typing import Any

from PySide6.QtWidgets import QMessageBox, QWidget


def _format_target(target: Any, index: int) -> str:
    """逐行渲染目标；pid 优先用展示串，避免把未找到的哨兵值 -1 直接显示给用户。"""
    if isinstance(target, dict):
        name = str(target.get("name") or "未知")
        pid = str(target.get("pid_display") or target.get("pid") or "未知")
        if pid in {"-1", "None"}:
            pid = "未找到"
        path = str(target.get("path") or "路径不可读")
        return f"{index}. {name}  PID {pid}  {path}"
    return f"{index}. {target}"


class ConfirmDialog:
    """只返回审批布尔值，不在对话线程执行任何操作。"""

    @staticmethod
    def ask(parent: QWidget, request: dict[str, Any]) -> bool:
        """逐行列出完整目标，按钮文案使用具体动作。"""
        dialog = QMessageBox(parent)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("需要你确认")
        targets = list(request.get("targets") or [])
        warnings = [str(item) for item in request.get("warnings") or []]
        detail = str(request.get("detail") or "即将执行破坏性操作")
        lines = [detail, "", "目标："]
        lines.extend(_format_target(target, index) for index, target in enumerate(targets, 1))
        if warnings:
            lines.extend(["", "额外提示：", *warnings])
        dialog.setText("\n".join(lines))
        action_text = str(request.get("action_text") or "执行该操作")
        approve_button = dialog.addButton(action_text, QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton("取消，不做改动", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(dialog.buttons()[-1])
        dialog.exec()
        return dialog.clickedButton() is approve_button
