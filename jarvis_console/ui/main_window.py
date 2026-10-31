"""融合版主窗口：HUD 为顶层容器，既有控制台子面板可收起叠加；接入语音 / 状态 / 提醒 / 设置。

实现要点（对齐 arch §2.1 修改清单与 PRD §5）：
- 中央部件换为 ``HUD``（QWebEngineView + WebSocket 桥）；Chat / Trace / 引擎徽标 / 状态条
  作为可收起 ``QDockWidget`` 或顶栏控件叠加，满足「叠加而非取代」（PRD §5.2 / Q9）。
- 复用既有 AgentWorker / TTSWorker / StatusWorker / ReminderWorker / VoiceController，
  不重写推理循环与大脑。
- 优雅降级：WebEngine 缺失则 HUD 退化为占位标签（文字通道仍可用）；麦克风 / 模型缺失则
  VoiceController 不启动（仅文字驱动 HUD 四态）；任何 worker 异常不阻断主链路。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..logging_setup import get_logger
from ..speech.tts import get_backend
from ..speech.voiceprint import Voiceprint
from .chat_view import ChatView
from .confirm_dialog import ConfirmDialog
from .hologram import HologramDemoController
from .provider_panel import ProviderSelector
from .reminder_dialog import ReminderDialog
from .setup_wizard import is_first_run, run_first_run
from .skill_confirm_dialog import SkillConfirmDialog, SkillProposal
from .skins import DEFAULT_SKIN, SkinPicker
from .status_bar import ConsoleStatusBar
from .trace_view import TraceView
from .voiceprint_dialog import VoiceprintEnrollDialog
from .widgets import ControlPanel, EngineBadges, IngestBanner, MemoryIndicator
from ..workers.agent_worker import AgentWorker, NoteRevokeWorker
from ..workers.tts_worker import TTSWorker
from ..workers.voice_controller import STATE_IDLE, VoiceController

log = get_logger(__name__)


class CommandInput(QPlainTextEdit):
    """Enter 发送、Shift+Enter 换行的输入框。"""

    sig_submit = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.sig_submit.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def _resolve_gate(gate: object, result: object) -> None:
    """兼容 SafetyGate 与裸 ConfirmationGate 两种上下文形态。"""
    confirm_gate = getattr(gate, "confirm_gate", gate)
    resolve = getattr(confirm_gate, "resolve", None)
    if callable(resolve):
        resolve(result)


class MainWindow(QMainWindow):
    """融合版总控台主窗口；严禁直接调用 LLM、网络或写 SQLite。"""

    def __init__(
        self,
        worker_factory: Callable[[str, bool, int], AgentWorker],
        revoke_fn: Callable[[str], bool],
        kb_count_fn: Callable[[], int],
        path_authorizer: Callable[[str], str],
        summarizer: Callable[[str], str] | None,
        *,
        deepseek_configured: bool,
        speech_enabled: bool,
        default_max_rounds: int,
        voice: str,
        speech_threshold: int,
        summary_max_chars: int,
        initial_kb_count: int,
        router: Any = None,
        audio: Any = None,
        voiceprint: Voiceprint | None = None,
        tts_backend: Any = None,
        status_worker: Any = None,
        reminder_worker: Any = None,
        default_skin: str = DEFAULT_SKIN,
    ) -> None:
        super().__init__()
        self.setWindowTitle("JARVIS · 融合版总控台")
        self.resize(1360, 880)

        self.worker: AgentWorker | None = None
        self.tts_worker: TTSWorker | None = None
        self.revoke_worker: NoteRevokeWorker | None = None
        self.skill_manager: Any = None
        self._worker_factory = worker_factory
        self._revoke_fn = revoke_fn
        self._kb_count_fn = kb_count_fn
        self._path_authorizer = path_authorizer
        self._summarizer = summarizer
        self._voice = voice
        self._speech_threshold = speech_threshold
        self._summary_max_chars = summary_max_chars
        self._kb_before = initial_kb_count
        self._pending_content = ""
        self._default_skin = default_skin or DEFAULT_SKIN

        self.router = router
        self.audio = audio
        self.voiceprint = voiceprint or Voiceprint()
        self.tts_backend = tts_backend or get_backend("edge", voice)
        self.status_worker = status_worker
        self.reminder_worker = reminder_worker
        self.voice_controller: VoiceController | None = None

        # —— 既有控制台子面板（保留，作为可收起叠加层）——
        self.chat = ChatView()
        self.trace = TraceView()
        self.badges = EngineBadges(deepseek_configured)
        self.controls = ControlPanel(speech_enabled)
        self.memory_indicator = MemoryIndicator()
        self.ingest_banner = IngestBanner()
        self.status = ConsoleStatusBar(initial_kb_count)
        self.input = CommandInput()
        self.input.setPlaceholderText("说出你的需求…（Enter 发送 / Shift+Enter 换行）")
        self.input.setMaximumHeight(100)
        self.send_button = QPushButton("发送")
        self.stop_button = QPushButton("■ 停止")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setEnabled(False)
        self.microphone_button = QPushButton("语音输入不可用")
        self.microphone_button.setEnabled(False)
        self.microphone_button.setToolTip("未检测到输入设备或未配置模型，本版仅支持文字")
        self.rounds = _RoundsSpinner(default_max_rounds)
        self.hud: Any = None

        self._build_central_and_docks()
        self._build_toolbar(router, default_skin)
        self._connect_ui()
        self._connect_workers()
        self.chat.append_notice(
            "已就绪。抖音站内搜索未启用；相关请求会诚实改用 B站 + 网页搜索。"
        )

    # —— 布局：HUD 中央 + 控制台可收起 docks ——
    def _build_central_and_docks(self) -> None:
        self.hud = self._make_hud()
        if self.hud is not None:
            self.setCentralWidget(self.hud)
        else:
            placeholder = QLabel("HUD 不可用（缺少 WebEngine）；文字通道仍可使用。")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setCentralWidget(placeholder)

        chat_dock = QDockWidget("对话", self)
        chat_dock.setWidget(self.chat)
        chat_dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, chat_dock)

        trace_dock = QDockWidget("过程", self)
        trace_dock.setWidget(self.trace)
        trace_dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, trace_dock)

        # 底部：沉淀提示 + 状态条 + 输入框 + 按钮
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.addWidget(self.ingest_banner)
        bl.addWidget(self.status)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        buttons = QVBoxLayout()
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.send_button)
        btn_row.addWidget(self.stop_button)
        row2 = QHBoxLayout()
        row2.addWidget(self.rounds)
        row2.addWidget(self.microphone_button)
        buttons.addLayout(btn_row)
        buttons.addLayout(row2)
        row.addLayout(buttons)
        bl.addLayout(row)
        bottom_dock = QDockWidget("控制台", self)
        bottom_dock.setWidget(bottom)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, bottom_dock)

    def _make_hud(self) -> Any:
        """懒加载 HUD；若 PySide6 WebEngine 不可用则降级返回 None。"""
        try:
            from .hud import HUD

            hud = HUD(self)
            if self._default_skin:
                hud.set_skin(self._default_skin)
            return hud
        except Exception as exc:  # noqa: BLE001 - WebEngine 缺失等
            log.warning("HUD 初始化失败，降级为文字通道：%s", exc)
            return None

    def _build_toolbar(self, router: Any, default_skin: str) -> None:
        toolbar = self.addToolBar("设置")
        toolbar.setMovable(False)
        toolbar.addWidget(self.badges)
        toolbar.addSeparator()
        toolbar.addWidget(self.memory_indicator)
        toolbar.addSeparator()
        # 多服务商选择器（N 路）
        if router is not None:
            self.provider_selector = ProviderSelector(router, self)
            self.provider_selector.sig_provider.connect(
                lambda pid: self.badges.set_active(str(pid), "手动切换")
            )
            toolbar.addWidget(self.provider_selector)
        # 皮肤选择器（八套，可手选）
        self.skin_picker = SkinPicker(current=self._default_skin, parent=self)
        self.skin_picker.sig_skin.connect(self._on_skin_changed)
        toolbar.addWidget(self.skin_picker)
        # 声纹 / 提醒 / 设置 入口
        self.voiceprint_button = QPushButton("声纹")
        self.voiceprint_button.setToolTip("注册 / 重注册主人声纹")
        self.voiceprint_button.clicked.connect(self._open_voiceprint)
        toolbar.addWidget(self.voiceprint_button)
        self.reminder_button = QPushButton("提醒")
        self.reminder_button.setToolTip("新建 / 查看 / 取消提醒")
        self.reminder_button.clicked.connect(self._open_reminder)
        toolbar.addWidget(self.reminder_button)
        self.settings_button = QPushButton("设置")
        self.settings_button.setToolTip("重新打开首启向导（引擎 / 语音 / 皮肤）")
        self.settings_button.clicked.connect(self._open_settings)
        toolbar.addWidget(self.settings_button)

    def _connect_ui(self) -> None:
        self.input.sig_submit.connect(self.submit)
        self.send_button.clicked.connect(self.submit)
        self.stop_button.clicked.connect(self.stop)
        self.ingest_banner.sig_revoke.connect(self._start_revoke)
        self.controls.sig_authorize_requested.connect(self._authorize_directory)

    # —— 后台 worker 接线 ——
    def _connect_workers(self) -> None:
        # HUD ↔ Python 消息桥
        if self.hud is not None:
            self.hud.sig_message.connect(self._on_hud_message)
        # 语音编排控制器（仅当麦克风 / 音频链路可用时启动）
        if self.audio is not None:
            self._start_voice_controller()
        # 系统状态常驻（每秒推 HUD 顶栏）
        if self.status_worker is not None:
            self.status_worker.sig_status.connect(self._on_sys_status)
            self.status_worker.start()
        # 日程到点触发（TTS + HUD 弹层）
        if self.reminder_worker is not None:
            self.reminder_worker.sig_due.connect(self._on_reminder_due)
            self.reminder_worker.start()
        # 技能管理器（学会新能力闭环）
        if self.router is not None:
            try:
                from ..skills.learn import SkillManager

                self.skill_manager = SkillManager(self.router)
            except Exception as exc:  # noqa: BLE001
                log.warning("技能管理器初始化失败：%s", exc)

    def _start_voice_controller(self) -> None:
        try:
            self.voice_controller = VoiceController(
                audio=self.audio,
                voiceprint=self.voiceprint,
                worker_factory=self._worker_factory,
                voice=self._voice,
                tts_backend=self.tts_backend,
                summarizer=self._summarizer,
                threshold=self._speech_threshold,
                summary_max_chars=self._summary_max_chars,
                max_rounds=self.rounds.value(),
                voiceprint_required=self.controls.voiceprint_box.isChecked()
                if hasattr(self.controls, "voiceprint_box")
                else True,
                parent=self,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("VoiceController 初始化失败：%s", exc)
            return
        # 四态 → HUD
        self.voice_controller.sig_state.connect(self._on_vc_state)
        self.voice_controller.sig_subtitle.connect(self._on_vc_subtitle)
        self.voice_controller.sig_message.connect(
            lambda role, text: self.chat.append_message(role, text)
        )
        self.voice_controller.sig_agent_outcome.connect(self._on_agent_outcome)
        self.voice_controller.sig_engine.connect(self._on_engine_changed)
        self.voice_controller.sig_log.connect(
            lambda msg: self.chat.append_notice(msg)
        )
        self.microphone_button.setEnabled(True)
        self.microphone_button.setText("语音就绪 · Hey Jarvis")
        self.microphone_button.setToolTip("已接入语音链路；说 “Hey Jarvis” 唤醒")
        self.voice_controller.start()

    # —— 文字命令（命令行输入框 / HUD 文字通道共用）——
    def submit(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self._run_agent(text)

    def _run_agent(self, text: str) -> None:
        """创建一次性 AgentWorker 并连接信号；同时驱动 HUD 四态（文字通道）。"""
        if self.worker is not None and self.worker.isRunning():
            return
        self.chat.append_message("user", text)
        self.trace.begin_task(text)
        self._pending_content = ""
        self._kb_before = self._safe_kb_count()
        self._hud_push_state(STATE_IDLE if False else "thinking", "思考中…")
        self.worker = self._worker_factory(text, self.controls.trust.isChecked(), self.rounds.value())
        self.worker.sig_token.connect(self.on_token)
        self.worker.sig_tool_started.connect(self.trace.on_tool_started)
        self.worker.sig_tool_finished.connect(self.on_tool_finished)
        self.worker.sig_confirm_needed.connect(self.on_confirm_needed)
        self.worker.sig_status.connect(self.status.set_step)
        self.worker.sig_engine_changed.connect(self.on_engine_changed)
        self.worker.sig_finished.connect(self.on_finished)
        self.worker.sig_error.connect(self.on_error)
        self.worker.sig_skill_proposal.connect(self._on_skill_proposal)
        self.worker.finished.connect(self.worker.deleteLater)
        self._set_busy(True)
        self.worker.start()

    def stop(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()

    def on_token(self, text: str) -> None:
        self._pending_content = text

    def on_tool_finished(self, payload: dict) -> None:
        self.trace.on_tool_finished(payload)
        if not payload.get("ok"):
            self.status.set_step(f"工具 {payload.get('name', '')} 未成功，已回灌给模型")

    def on_confirm_needed(self, request: dict) -> None:
        approved = ConfirmDialog.ask(self, request)
        gate = getattr(getattr(self.worker, "ctx", None), "gate", None)
        if gate is not None:
            _resolve_gate(gate, {"approved": approved})

    def on_engine_changed(self, payload: dict) -> None:
        self.badges.set_active(str(payload.get("engine") or ""), str(payload.get("cause") or ""))
        self._hud_set_engine(str(payload.get("engine") or ""))

    def on_finished(self, outcome: dict) -> None:
        answer = str(outcome.get("answer") or self._pending_content or "本轮没有生成答复。")
        self.chat.append_message("assistant", answer)
        self.trace.finish_task(not bool(outcome.get("error")))
        self.memory_indicator.set_hits(list(outcome.get("memory_hits") or []))
        note_ids = [str(item) for item in outcome.get("ingested_note_ids") or [] if item]
        if note_ids:
            self.ingest_banner.show_notes(note_ids)
        remembered = list(outcome.get("remembered_ids") or [])
        if remembered:
            self.chat.append_notice(f"已记住 {len(remembered)} 条长期偏好，可在后续会话自动召回。")
        after_count = self._safe_kb_count()
        self.status.set_kb_transition(self._kb_before, after_count)
        self.status.set_usage(
            int(outcome.get("prompt_tokens", 0) or 0),
            int(outcome.get("completion_tokens", 0) or 0),
        )
        self.status.set_step("已停止" if outcome.get("cancelled") else "完成")
        self._hud_push_state("speaking", answer[:80] if answer else "播报中…", str(outcome.get("engine") or ""))
        self._set_busy(False)
        self.worker = None
        if self.controls.speech.isChecked() and answer:
            self._start_tts(answer)
        # 播报结束后回 idle（HUD）
        QTimer.singleShot(3500, lambda: self._hud_push_state(STATE_IDLE, "待命中"))

    def on_error(self, payload: dict) -> None:
        self.chat.append_notice(
            f"本轮异常：{payload.get('type', 'Error')} {payload.get('message', '')}",
            is_error=True,
        )
        self.trace.finish_task(False)
        self._hud_push_state("speaking", "出错了")
        self._set_busy(False)
        self.worker = None

    def _start_tts(self, answer: str) -> None:
        if self.tts_worker is not None and self.tts_worker.isRunning():
            return
        self.tts_worker = TTSWorker(
            answer,
            voice=self._voice,
            summarizer=self._summarizer,
            threshold=self._speech_threshold,
            summary_max_chars=self._summary_max_chars,
            backend=self.tts_backend,
            parent=self,
        )
        self.tts_worker.sig_error.connect(
            lambda payload: self.chat.append_notice(f"播报失败：{payload.get('message', '')}", True)
        )
        self.tts_worker.finished.connect(self.tts_worker.deleteLater)
        self.tts_worker.start()

    def _start_revoke(self, note_ids: list[str]) -> None:
        if self.revoke_worker is not None and self.revoke_worker.isRunning():
            return
        self.revoke_worker = NoteRevokeWorker(note_ids, self._revoke_fn, self)
        self.revoke_worker.sig_finished.connect(self._on_revoke_finished)
        self.revoke_worker.finished.connect(self.revoke_worker.deleteLater)
        self.revoke_worker.start()

    def _on_revoke_finished(self, payload: dict) -> None:
        results = dict(payload.get("results") or {})
        succeeded = [note_id for note_id, ok in results.items() if ok]
        self.ingest_banner.mark_revoked(succeeded)
        if payload.get("error"):
            self.chat.append_notice(f"撤回失败：{payload['error']}", True)
        else:
            self.chat.append_notice(f"已撤回 {len(succeeded)} 条知识库沉淀。")
        self.revoke_worker = None

    def _authorize_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择本次运行允许写入的目录")
        if selected:
            authorized = self._path_authorizer(selected)
            self.chat.append_notice(f"本次运行已授权写入：{authorized}")

    # —— HUD 桥接线 ——
    def _hud_push_state(self, state: str, subtitle: str = "", engine: str = "") -> None:
        if self.hud is not None:
            self.hud.push_state(state, subtitle, engine)

    def _hud_set_engine(self, engine: str) -> None:
        if self.hud is not None:
            self.hud.push_state(state="_engine", subtitle="", engine=engine)

    def _on_hud_message(self, message: dict) -> None:
        if not isinstance(message, dict):
            return
        msg_type = str(message.get("type") or "")
        if msg_type == "text":
            text = str(message.get("text") or "").strip()
            if text:
                self._run_agent(text)
        elif msg_type == "interrupt":
            # HUD 上“打断”按钮：若在语音会话中，回到聆听态（免唤醒追问）。
            if self.voice_controller is not None:
                self.voice_controller._interrupt_to_listening()

    def _on_sys_status(self, sys: dict) -> None:
        if self.hud is not None:
            self.hud.update_sys(sys)

    def _on_reminder_due(self, due: list) -> None:
        for item in due:
            text = str(item.get("text") or "提醒")
            self.chat.append_notice(f"⏰ 提醒：{text}")
            self._hud_push_state("speaking", f"提醒：{text}")
            if self.controls.speech.isChecked():
                self._start_tts(f"提醒：{text}")

    def _on_skill_proposal(self, data: dict) -> None:
        proposal = data.get("skill_proposal") or data
        spec = SkillProposal.from_dict(proposal)
        if not SkillConfirmDialog.ask(self, spec):
            self.chat.append_notice("已取消安装该技能。")
            return
        if self.skill_manager is None:
            self.chat.append_notice("技能管理器不可用，无法安装。", is_error=True)
            return
        ok, msg = self.skill_manager.install(_spec_from_proposal(spec))
        self.chat.append_notice(msg, is_error=not ok)

    def _on_vc_state(self, state: str, subtitle: str, engine: str) -> None:
        self._hud_push_state(state, subtitle, engine)

    def _on_vc_subtitle(self, text: str, is_final: bool) -> None:
        if self.hud is not None:
            self.hud.update_subtitle(text, is_final)

    def _on_agent_outcome(self, outcome: dict) -> None:
        # 复用与文字通道一致的结果落地（记忆 / 沉淀 / 用量）。
        self.memory_indicator.set_hits(list(outcome.get("memory_hits") or []))
        note_ids = [str(item) for item in outcome.get("ingested_note_ids") or [] if item]
        if note_ids:
            self.ingest_banner.show_notes(note_ids)
        self.status.set_usage(
            int(outcome.get("prompt_tokens", 0) or 0),
            int(outcome.get("completion_tokens", 0) or 0),
        )

    # —— 设置入口 ——
    def _on_skin_changed(self, skin_id: str) -> None:
        if self.hud is not None:
            self.hud.set_skin(skin_id)
        self._persist_skin(skin_id)

    def _persist_skin(self, skin_id: str) -> None:
        try:
            from .setup_wizard import _apply_config_block

            _apply_config_block({"ui": {"default_skin": skin_id}})
            from ..config import get_config

            get_config(reload=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("皮肤偏好写回失败：%s", exc)

    def _open_voiceprint(self) -> None:
        VoiceprintEnrollDialog(self).exec()

    def _open_reminder(self) -> None:
        ReminderDialog(self).exec()

    def _open_settings(self) -> None:
        run_first_run(self)

    def _set_busy(self, busy: bool) -> None:
        self.input.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.rounds.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        if not busy and self.voice_controller is None:
            self.input.setFocus()

    def _safe_kb_count(self) -> int:
        try:
            return int(self._kb_count_fn())
        except Exception:
            return self._kb_before

    def closeEvent(self, event: QCloseEvent) -> None:
        running = self.worker is not None and self.worker.isRunning()
        if running:
            answer = QMessageBox.question(
                self,
                "任务仍在运行",
                "是否停止当前任务并退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(3000)
        if self.voice_controller is not None:
            self.voice_controller.stop()
        if self.status_worker is not None:
            self.status_worker.stop()
        if self.reminder_worker is not None:
            self.reminder_worker.stop()
        if self.hud is not None:
            self.hud.shutdown()
        event.accept()


class _RoundsSpinner(QWidget):
    """最大轮次选择器；本身即 QWidget（内含 QSpinBox），可直接加入布局。"""

    def __init__(self, value: int = 8, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._box = QSpinBox(self)
        self._box.setRange(1, 16)
        self._box.setValue(value)
        self._box.setPrefix("最大轮次 ")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._box)

    def value(self) -> int:
        return int(self._box.value())

    def isEnabled(self) -> bool:
        return self._box.isEnabled()

    def setEnabled(self, enabled: bool) -> None:
        self._box.setEnabled(enabled)


def _spec_from_proposal(proposal: SkillProposal) -> Any:
    """把 SkillProposal 适配为 skills.learn.SkillSpec。"""
    from ..skills.learn import SkillSpec

    return SkillSpec(
        name=proposal.name,
        description=proposal.description,
        code=proposal.code,
        sha256=proposal.sha256,
    )
