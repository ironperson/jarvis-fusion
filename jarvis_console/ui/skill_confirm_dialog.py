"""“学会新能力”确认弹窗：展示代码 + 影响范围，主人确认后才安装。"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


@dataclass
class SkillProposal:
    """从 learn_skill 工具或 SkillManager 产出的确认载荷。"""

    name: str
    description: str
    code: str
    sha256: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillProposal":
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            code=str(data.get("code", "")),
            sha256=str(data.get("sha256", "")),
        )

    def impact_scope(self) -> str:
        """解析代码的影响范围（import 了哪些模块、是否会写文件）。"""
        lines = ["影响范围评估："]
        try:
            tree = ast.parse(self.code)
            imports = []
            writes = False
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mod = getattr(node, "module", None) or ",".join(a.name for a in node.names)
                    imports.append(mod)
                if isinstance(node, ast.Attribute) and node.attr in {"write", "writelines", "open"}:
                    writes = True
            lines.append("· 导入模块：" + (", ".join(sorted(set(imports))) or "无"))
            lines.append("· 文件写入：" + ("存在，需确认落在白名单内" if writes else "未发现"))
        except SyntaxError:
            lines.append("· 代码无法静态解析，请谨慎确认。")
        lines.append(f"· 代码指纹 SHA256：{self.sha256[:16]}…（用于审计）")
        return "\n".join(lines)


class SkillConfirmDialog(QDialog):
    """展示技能代码与影响范围，返回审批布尔值。"""

    def __init__(self, proposal: SkillProposal, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.proposal = proposal
        self.setWindowTitle(f"确认安装新技能：{proposal.name}")
        self.resize(640, 520)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"技能名称：{proposal.name}"))
        layout.addWidget(QLabel(f"用途：{proposal.description}"))

        layout.addWidget(QLabel("技能代码（只读）："))
        code_view = QPlainTextEdit()
        code_view.setPlainText(proposal.code)
        code_view.setReadOnly(True)
        code_view.setMinimumHeight(220)
        layout.addWidget(code_view, 1)

        impact = QTextBrowser()
        impact.setPlainText(proposal.impact_scope())
        layout.addWidget(impact)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText("确认安装")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    @staticmethod
    def ask(parent: QWidget | None, proposal: SkillProposal) -> bool:
        dialog = SkillConfirmDialog(proposal, parent)
        return dialog.exec() == QDialog.DialogCode.Accepted
