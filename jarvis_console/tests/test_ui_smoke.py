"""UI 层冒烟测试：本机装好 PySide6 时验证窗口、过程面板与确认握手。"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest


def test_main_window_confirm_handshake(tmp_path: Path) -> None:
    """验证弹窗结果能经 ConfirmationGate 跨线程回灌给 worker。"""
    try:
        import PySide6  # noqa: F401
    except ImportError:
        pytest.skip("未安装 PySide6，跳过 UI 冒烟测试")

    from PySide6.QtWidgets import QApplication

    from jarvis_console.core.safety import ConfirmationGate, PathPolicy, SafetyGate
    from jarvis_console.core.types import ConfirmRequest, TurnContext
    from jarvis_console.ui import main_window as main_window_module
    from jarvis_console.ui.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow(
        worker_factory=lambda text, trust_mode, max_rounds: None,
        revoke_fn=lambda note_id: True,
        kb_count_fn=lambda: 0,
        path_authorizer=lambda path: path,
        summarizer=None,
        deepseek_configured=False,
        speech_enabled=False,
        default_max_rounds=8,
        voice="zh-CN-YunjianNeural",
        speech_threshold=200,
        summary_max_chars=80,
        initial_kb_count=0,
    )
    pending: list[dict] = []
    approved_result: list[bool] = []
    last_request: list[dict] = []

    def record(payload: dict) -> None:
        """模拟 sig_confirm_needed：仅把请求排队，弹窗必须由主线程执行。"""
        pending.append(payload)

    gate = ConfirmationGate(record)
    # 生产 AgentWorker 塞进 ctx.gate 的是 SafetyGate，这里必须同构，测到真正会跑的分支。
    safety_gate = SafetyGate(
        PathPolicy(allowed_roots=[tmp_path], sensitive_dirs=[]), gate, confirmation_timeout=5.0
    )
    window.worker = type(
        "Stub", (), {"ctx": TurnContext("x", gate=safety_gate), "isRunning": lambda self: False}
    )()
    original_ask = main_window_module.ConfirmDialog.ask
    main_window_module.ConfirmDialog.ask = staticmethod(
        lambda parent, request: (last_request.append(dict(request)), True)[-1]
    )

    def ask_from_thread() -> None:
        approved = gate.ask(
            ConfirmRequest("delete_file", "删除这 1 个文件", ["C:/tmp/a.txt"]), timeout=5.0
        )
        approved_result.append(isinstance(approved, dict) and approved.get("approved") is True)

    worker_thread = threading.Thread(target=ask_from_thread, daemon=True)
    try:
        worker_thread.start()
        deadline = 300
        while not pending and deadline > 0:
            application.processEvents()
            threading.Event().wait(0.02)
            deadline -= 1
        while pending:
            window.on_confirm_needed(pending.pop(0))
            application.processEvents()
        worker_thread.join(timeout=10)
        assert approved_result and approved_result[-1] is True, "弹窗确认没有回灌给 worker"
        assert last_request and last_request[-1]["tool_name"] == "delete_file"
    finally:
        main_window_module.ConfirmDialog.ask = original_ask
        worker_thread.join(timeout=1)

    window.trace.begin_task("冒烟任务")
    window.trace.on_tool_started({"id": "c1", "name": "demo", "arguments": {"a": 1}})
    window.trace.on_tool_finished({
        "tool_call_id": "c1",
        "name": "demo",
        "ok": False,
        "text": "",
        "error": "boom",
        "elapsed_ms": 12.0,
    })
    window.trace.finish_task(False)
    assert window.trace.topLevelItem(0) is not None
    window.chat.append_message("assistant", "| 列A | 列B |\n|---|---|")
    window.chat.append_notice("冒烟提示")
    window.close()
