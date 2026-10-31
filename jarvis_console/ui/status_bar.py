"""总控台底部状态信息。"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..core.metrics import CostEstimate, estimate_cost


class ConsoleStatusBar(QWidget):
    """显示步骤、tokens、成本估算与知识库计数。"""

    def __init__(self, initial_kb_count: int = 0, parent: object | None = None) -> None:
        super().__init__(parent)
        self.step_label = QLabel("就绪")
        self.token_label = QLabel("tokens 0")
        self.cost_label = QLabel("本轮 ≈¥0.0000（闲时）")
        self.kb_label = QLabel(f"KB {initial_kb_count}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(self.step_label, 1)
        layout.addWidget(self.token_label)
        layout.addWidget(self.cost_label)
        layout.addWidget(self.kb_label)

    def set_step(self, text: str) -> None:
        self.step_label.setText(text)

    def set_usage(self, prompt_tokens: int, completion_tokens: int) -> CostEstimate:
        estimate = estimate_cost(prompt_tokens, completion_tokens)
        self.token_label.setText(f"tokens {prompt_tokens + completion_tokens:,}")
        self.cost_label.setText(
            f"本轮 ≈¥{estimate.cost_rmb:.4f}（{'峰时' if estimate.peak else '闲时'}）"
        )
        return estimate

    def set_kb_transition(self, before: int, after: int) -> None:
        self.kb_label.setText(f"KB {before}→{after}" if before != after else f"KB {after}")
