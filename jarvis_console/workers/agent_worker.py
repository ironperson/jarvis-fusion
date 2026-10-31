"""一次完整 Turn 的 QThread 包装。"""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QThread, Signal, Slot

from ..core.agent_loop import AgentLoop
from ..core.llm_client import EngineRouter
from ..core.memory import MemoryStore
from ..core.safety import ConfirmationGate, SafetyGate
from ..core.tool_registry import ToolRegistry
from ..core.types import TurnContext


class AgentWorker(QThread):
    """在 Qt 主线程外运行 LLM、工具、网络和 SQLite。"""

    sig_token = Signal(str)
    sig_tool_started = Signal(dict)
    sig_tool_finished = Signal(dict)
    sig_confirm_needed = Signal(dict)
    sig_status = Signal(str)
    sig_engine_changed = Signal(dict)
    sig_skill_proposal = Signal(dict)
    sig_finished = Signal(dict)
    sig_error = Signal(dict)

    def __init__(
        self,
        registry: ToolRegistry,
        router: EngineRouter,
        memory: MemoryStore,
        user_text: str,
        *,
        trust_mode: bool = False,
        max_rounds: int = 8,
        timeout: int = 120,
        confirmation_timeout: float = 300.0,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.user_text = user_text
        confirm_gate = ConfirmationGate(self.sig_confirm_needed.emit)
        safety_gate = SafetyGate(
            confirm_gate=confirm_gate,
            trust_mode=trust_mode,
            confirmation_timeout=confirmation_timeout,
        )
        self.ctx = TurnContext(user_text=user_text, trust_mode=trust_mode, gate=safety_gate)
        self._router = router
        self._initial_engine = router.active.name
        self._loop = AgentLoop(
            registry=registry,
            router=router,
            memory=memory,
            max_rounds=max_rounds,
            timeout=timeout,
            on_status=self.sig_status.emit,
            on_tool_started=self.sig_tool_started.emit,
            on_tool_finished=self.sig_tool_finished.emit,
            on_token=self.sig_token.emit,
            on_skill_proposal=lambda payload: self.sig_skill_proposal.emit(payload),
        )

    @Slot()
    def run(self) -> None:
        """执行 AgentLoop 并保证仅发基础类型信号载荷。"""
        try:
            outcome = self._loop.run(self.user_text, self.ctx)
            if self._router.active.name != self._initial_engine or outcome.engine:
                self.sig_engine_changed.emit({
                    "engine": outcome.engine or self._router.active.name,
                    "cause": "本轮模型路由结果",
                })
            self.sig_finished.emit(outcome.to_dict())
        except Exception as exc:
            self.sig_error.emit({
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            })

    @Slot()
    def cancel(self) -> None:
        """协作式停止，并释放可能正在等待的确认门。"""
        self.ctx.cancel()
        gate = getattr(self.ctx, "gate", None)
        if gate is not None:
            confirm_gate = getattr(gate, "confirm_gate", gate)
            resolve = getattr(confirm_gate, "resolve", None)
            if callable(resolve):
                resolve(False)
        self.sig_status.emit("正在停止，将在当前工具边界退出…")


class NoteRevokeWorker(QThread):
    """在线程外撤回知识库笔记，避免 UI 主线程写 SQLite。"""

    sig_finished = Signal(dict)

    def __init__(self, note_ids: list[str], delete_fn: Callable[[str], bool], parent: object | None = None) -> None:
        super().__init__(parent)
        self.note_ids = list(note_ids)
        self.delete_fn = delete_fn

    @Slot()
    def run(self) -> None:
        results: dict[str, bool] = {}
        error = ""
        try:
            for note_id in self.note_ids:
                results[note_id] = bool(self.delete_fn(note_id))
        except Exception as exc:
            error = str(exc)
        self.sig_finished.emit({"results": results, "error": error})
