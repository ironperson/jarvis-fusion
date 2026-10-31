"""只读对话视图。"""
from __future__ import annotations

import html
import re

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextEdit


class ChatView(QTextEdit):
    """安全渲染用户与助手消息的轻量 Markdown 视图。"""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(True)
        self.setPlaceholderText("在下方输入需求，小王老师会自己挑工具完成。")

    @staticmethod
    def _render_text(text: str) -> str:
        escaped = html.escape(text)
        escaped = re.sub(r"```([\s\S]*?)```", r"<pre>\1</pre>", escaped)
        lines = escaped.splitlines()
        rendered: list[str] = []
        in_table = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                if not in_table:
                    rendered.append("<pre>")
                    in_table = True
                rendered.append(line)
            else:
                if in_table:
                    rendered.append("</pre>")
                    in_table = False
                if stripped.startswith("# "):
                    rendered.append(f"<h3>{stripped[2:]}</h3>")
                elif stripped.startswith(("- ", "* ")):
                    rendered.append(f"• {stripped[2:]}<br>")
                else:
                    rendered.append(f"{line}<br>")
        if in_table:
            rendered.append("</pre>")
        return "".join(rendered)

    def append_message(self, role: str, text: str) -> None:
        """追加一条带角色标题的消息并滚动到底部。"""
        label = "我" if role == "user" else "小王老师"
        role_class = "user" if role == "user" else "assistant"
        block = (
            f'<div class="message {role_class}"><b>{label}</b><br>'
            f"{self._render_text(text)}</div><br>"
        )
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(block)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def append_notice(self, text: str, is_error: bool = False) -> None:
        """追加状态/错误提示。"""
        color = "#ff6b6b" if is_error else "#8aa4c8"
        self.append(f'<span style="color:{color}">{html.escape(text)}</span>')
