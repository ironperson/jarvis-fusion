"""语音编排核心（QThread）：四态状态机 + barge-in 打断 + 复用 AgentWorker/TTSWorker。

状态机：idle → listening → thinking → speaking → idle。
- 唤醒命中 → 声纹校验 → 通过才进 listening（非主人静默忽略）。
- listening：ASR 流式字幕；静音断句产出 final 文本后转 thinking。
- thinking：跑 AgentWorker；完成转 speaking，回灌字幕。
- speaking：TTS 播放；播放/思考中被新语音能量打断 → 立即停 TTS 转 listening（免唤醒追问）。
- 超时静音（默认 8s）→ 回 idle。
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QThread, QTimer, Signal, Slot

from ..core.types import TurnOutcome
from ..logging_setup import get_logger
from ..speech.audio_service import AudioService
from ..speech.tts import TTSBackend
from ..speech.voiceprint import Voiceprint

log = get_logger(__name__)

# 四态常量。
STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"

# 超时（毫秒）：进入 listening/thinking/speaking 后无新活动则回 idle。
INACTIVITY_TIMEOUT_MS = 8000


class VoiceController(QThread):
    """串联 AudioService → 声纹 → ASR → 复用 AgentWorker/TTSWorker 的语音编排器。"""

    # HUD 状态推送（四态 + 字幕 + 引擎）。
    sig_state = Signal(str, str, str)  # state, subtitle, engine
    # 实时字幕（ASR 增量/最终）。
    sig_subtitle = Signal(str, bool)  # text, is_final
    # 对话气泡（与文字通道共用 ChatView）。
    sig_message = Signal(str, str)  # role, text
    # Agent 完成结果（dict）。
    sig_agent_outcome = Signal(dict)
    # 引擎徽标变化。
    sig_engine = Signal(dict)
    # 状态机日志（可选展示）。
    sig_log = Signal(str)

    def __init__(
        self,
        audio: AudioService,
        voiceprint: Voiceprint,
        worker_factory: Callable[[str, bool, int], Any],
        voice: str = "en-GB-SoniaNeural",
        tts_backend: TTSBackend | None = None,
        summarizer: Callable[[str], str] | None = None,
        threshold: int = 200,
        summary_max_chars: int = 80,
        trust_mode: bool = False,
        max_rounds: int = 8,
        voiceprint_required: bool = True,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.audio = audio
        self.voiceprint = voiceprint
        self.worker_factory = worker_factory
        self.voice = voice
        self.tts_backend = tts_backend
        self.summarizer = summarizer
        self.threshold = threshold
        self.summary_max_chars = summary_max_chars
        self.trust_mode = trust_mode
        self.max_rounds = max_rounds
        self.voiceprint_required = voiceprint_required

        self._state = STATE_IDLE
        self._running = False
        self._agent: Any | None = None
        self._tts: Any | None = None
        self._lock = threading.RLock()
        self._inactivity_timer = QTimer(self)
        self._inactivity_timer.setSingleShot(True)
        self._inactivity_timer.timeout.connect(self._on_inactivity)

    # —— 生命周期 ——
    def run(self) -> None:
        self._running = True
        self._wire_audio()
        try:
            self.audio.start()
            self.sig_log.emit("语音链路已启动，等待 “Hey Jarvis”…")
        except Exception as exc:  # noqa: BLE001 - 麦克风缺失等，优雅降级
            self.sig_log.emit(f"语音链路不可用：{exc}（文字通道仍可用）")
        self._set_state(STATE_IDLE, "待命中")
        self.exec()

    def stop(self) -> None:
        self._running = False
        try:
            self.audio.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._agent is not None and self._agent.isRunning():
            self._agent.cancel()
        if self._tts is not None and self._tts.isRunning():
            self._tts.quit()
        self.quit()

    # —— 接线音频回调（统一汇入本线程的槽，保证状态机在自身线程执行）——
    def _wire_audio(self) -> None:
        self.audio.on_wake = self._on_wake_signal
        self.audio.on_asr = self._on_asr_signal
        self.audio.on_vad = self._on_vad_signal

    def _on_wake_signal(self, confidence: float, clip: Any) -> None:
        self._on_wake(confidence, clip)

    def _on_asr_signal(self, text: str, is_final: bool) -> None:
        self._on_asr(text, is_final)

    def _on_vad_signal(self, speech: bool, energy: float) -> None:
        self._on_vad(speech, energy)

    # —— 四态切换 ——
    def _set_state(self, state: str, subtitle: str = "", engine: str = "") -> None:
        with self._lock:
            self._state = state
        self.sig_state.emit(state, subtitle, engine)
        self._reset_inactivity()

    def _reset_inactivity(self) -> None:
        self._inactivity_timer.start(INACTIVITY_TIMEOUT_MS)

    # —— 唤醒处理 ——
    @Slot(float, object)
    def _on_wake(self, confidence: float, clip: Any) -> None:
        if not self._running:
            return
        with self._lock:
            if self._state == STATE_LISTENING or self._state == STATE_THINKING or self._state == STATE_SPEAKING:
                # 已在语音会话中，忽略重复唤醒（免唤醒追问期间不二次触发）。
                return
        # 声纹校验：启用且已注册时才校验；未注册或关闭校验则放行（降级）。
        if self.voiceprint_required and self.voiceprint.has_enrollment():
            result = self.voiceprint.verify(clip, sample_rate=16000)
            if not result.get("match", False):
                self.sig_log.emit(f"非主人声纹（score={result.get('score', 0):.2f}），静默忽略。")
                return
        self.sig_log.emit(f"唤醒命中（conf={confidence:.2f}），进入聆听。")
        self.audio.begin_listening()
        self._set_state(STATE_LISTENING, "聆听中…")

    # —— ASR 处理 ——
    @Slot(str, bool)
    def _on_asr(self, text: str, is_final: bool) -> None:
        if not self._running:
            return
        with self._lock:
            if self._state != STATE_LISTENING:
                # ASR 仅在聆听态有意义；其他态忽略（避免误触发）。
                return
        self.sig_subtitle.emit(text, is_final)
        if is_final and text.strip():
            self._begin_thinking(text.strip())

    # —— VAD 处理（barge-in 打断）——
    @Slot(bool, float)
    def _on_vad(self, speech: bool, energy: float) -> None:
        if not self._running:
            return
        with self._lock:
            state = self._state
        if (state == STATE_SPEAKING or state == STATE_THINKING) and speech:
            # 播放/思考中被新语音打断 → 停 TTS，转聆听（免唤醒追问）。
            self._interrupt_to_listening()

    def _interrupt_to_listening(self) -> None:
        with self._lock:
            if self._state not in (STATE_SPEAKING, STATE_THINKING):
                return
        self._stop_tts()
        self.audio.begin_listening()
        self._set_state(STATE_LISTENING, "被打断，继续聆听…")

    # —— 进入思考并跑 AgentWorker ——
    def _begin_thinking(self, text: str) -> None:
        with self._lock:
            if self._state != STATE_LISTENING:
                return
        self.sig_message.emit("user", text)
        self._set_state(STATE_THINKING, "思考中…")
        self._stop_tts()
        if self._agent is not None and self._agent.isRunning():
            self._agent.cancel()
        worker = self.worker_factory(text, self.trust_mode, self.max_rounds)
        worker.sig_finished.connect(self._on_agent_finished)
        worker.sig_error.connect(self._on_agent_error)
        worker.finished.connect(worker.deleteLater)
        self._agent = worker
        worker.start()

    @Slot(dict)
    def _on_agent_finished(self, outcome: dict) -> None:
        answer = str(outcome.get("answer") or "").strip()
        if not self._running:
            return
        self.sig_agent_outcome.emit(outcome)
        self.sig_message.emit("assistant", answer or "（无答复）")
        self._set_state(STATE_SPEAKING, answer[:80] if answer else "播报中…", str(outcome.get("engine") or ""))
        self.sig_engine.emit({"engine": outcome.get("engine") or "", "cause": "语音链路"})
        if answer:
            self._speak(answer)

    @Slot(dict)
    def _on_agent_error(self, payload: dict) -> None:
        self.sig_log.emit(f"语音 Agent 异常：{payload.get('message', '')}")
        self._set_state(STATE_IDLE, "出错了")

    # —— TTS ——
    def _speak(self, text: str) -> None:
        if self._tts is not None and self._tts.isRunning():
            self._tts.quit()
        from ..workers.tts_worker import TTSWorker

        worker = TTSWorker(
            text,
            voice=self.voice,
            summarizer=self.summarizer,
            threshold=self.threshold,
            summary_max_chars=self.summary_max_chars,
            backend=self.tts_backend,
            parent=self,
        )
        worker.sig_error.connect(lambda p: self.sig_log.emit(f"播报失败：{p.get('message', '')}"))
        worker.finished.connect(worker.deleteLater)
        self._tts = worker
        worker.start()

    def _stop_tts(self) -> None:
        if self._tts is not None and self._tts.isRunning():
            self._tts.quit()
            self._tts.wait(500)

    # —— 超时回 idle ——
    @Slot()
    def _on_inactivity(self) -> None:
        if not self._running:
            return
        with self._lock:
            state = self._state
        if state != STATE_IDLE:
            self.audio.end_listening(emit_final=False)
            self._set_state(STATE_IDLE, "待命中")

    # 让外部（如 StatusWorker）也能查询当前态，便于 HUD 联动。
    def current_state(self) -> str:
        with self._lock:
            return self._state
