"""麦克风采集 + 唤醒 / ASR / VAD 的线程编排。

职责边界（与 VoiceController 配合）：
- 本类只负责“持续采音 + 始终运行的唤醒检测 + 始终运行的 VAD + 受控的 ASR 分段识别”，
  并把事件通过回调推出；不持有四态状态机（那在 workers/voice_controller.py）。
- 缺 sounddevice / 模型时优雅降级：``start()`` 抛 DependencyMissing，调用方据此提示而非崩溃。
"""
from __future__ import annotations

import collections
import queue
import threading
from typing import Any, Callable

import numpy as np

from ..core.errors import DependencyMissing
from ..logging_setup import get_logger
from .asr import SAMPLE_RATE as ASR_RATE
from .vad import SAMPLE_RATE as VAD_RATE

log = get_logger(__name__)

# 唤醒命中时回传给声纹校验的音频长度（秒）。
_WAKE_CLIP_SECONDS = 2.5
# 端点静音超过该时长（秒）即认为一句话结束，产出 final。
_SILENCE_TIMEOUT_SECONDS = 0.8
# 增量 ASR 的解码节流间隔（秒）。
_INTERIM_INTERVAL_SECONDS = 0.5


class AudioService:
    """常驻音频管线：唤醒 + VAD + 受控 ASR。"""

    def __init__(
        self,
        sample_rate: int = 16000,
        wake: Any | None = None,
        asr: Any | None = None,
        vad: Any | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.wake = wake
        self.asr = asr
        self.vad = vad
        self.on_wake: Callable[[float, np.ndarray], None] | None = None
        self.on_asr: Callable[[str, bool], None] | None = None
        self.on_vad: Callable[[bool, float], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self._stream: Any | None = None
        self._frames: "queue.Queue[tuple[np.ndarray, int]]" = queue.Queue(maxsize=2000)
        self._running = False
        self._thread: threading.Thread | None = None
        self._listening = False
        self._asr_buffer: list[np.ndarray] = []
        self._silence_seconds = 0.0
        self._last_interim_seconds = 0.0
        self._rolling: "collections.deque[np.ndarray]" = collections.deque(
            maxlen=int(_WAKE_CLIP_SECONDS * sample_rate / 480) * 4
        )
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        """sounddevice 是否可用（决定麦克风链路能否启动）。"""
        try:
            import sounddevice  # type: ignore
        except ImportError:
            return False
        return True

    def start(self) -> None:
        """打开麦克风并开始处理线程；缺失依赖时抛 DependencyMissing。"""
        try:
            import sounddevice as sd  # type: ignore
        except ImportError as exc:
            raise DependencyMissing(
                "未安装 sounddevice，语音输入不可用；请执行 pip install sounddevice>=0.4.6。"
            ) from exc
        if self._running:
            return
        self._running = True
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=480,
                callback=self._input_callback,
            )
            self._stream.start()
        except Exception as exc:  # noqa: BLE001 - 无麦克风设备等多种异常
            self._running = False
            raise DependencyMissing(f"无法打开麦克风：{exc}") from exc
        self._thread = threading.Thread(target=self._process_loop, name="audio-service", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止采音与处理线程。"""
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def begin_listening(self) -> None:
        """VoiceController 进入聆听态时调用：开启 ASR 累积。"""
        with self._lock:
            self._listening = True
            self._asr_buffer = []
            self._silence_seconds = 0.0
            self._last_interim_seconds = 0.0
            if self.asr is not None:
                self.asr.reset()

    def end_listening(self, *, emit_final: bool = True) -> None:
        """退出聆听态：产出最终 ASR 文本（若有）并停止累积。"""
        with self._lock:
            was_listening = self._listening
            self._listening = False
            if emit_final and was_listening and self.asr is not None and self._asr_buffer:
                final = self.asr.finalize()
                if final and self.on_asr is not None:
                    self.on_asr(final, True)

    def _input_callback(self, indata: np.ndarray, frames: int, _time_info: Any, status: Any) -> None:
        if status:
            log.debug("音频输入状态：%s", status)
        try:
            self._frames.put_nowait((np.copy(indata[:, 0]).astype(np.float32), frames))
        except queue.Full:
            pass

    def _wake_clip(self) -> np.ndarray:
        with self._lock:
            return np.concatenate(list(self._rolling)) if self._rolling else np.zeros(0, dtype=np.float32)

    def _emit(self, callback: Callable[..., None] | None, *args: Any) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:  # noqa: BLE001 - 回调异常不应影响音频管线
            log.warning("音频回调异常：%s", exc)

    def _process_loop(self) -> None:
        while self._running:
            try:
                samples, n = self._frames.get(timeout=0.1)
            except queue.Empty:
                continue
            with self._lock:
                self._rolling.append(samples)
            # 唤醒检测：始终运行（离线 "Hey Jarvis"）。
            if self.wake is not None:
                try:
                    hit, confidence = self.wake.feed(samples)
                except Exception as exc:  # noqa: BLE001
                    log.warning("唤醒检测异常：%s", exc)
                    hit, confidence = False, 0.0
                if hit:
                    clip = self._wake_clip()
                    self._emit(self.on_wake, confidence, clip)
            # VAD：始终运行，供 barge-in 与免唤醒追问。
            if self.vad is not None:
                try:
                    speech = self.vad.is_speech(samples)
                except Exception:  # noqa: BLE001
                    speech = False
            else:
                speech = VAD_rms(samples)
            energy = _rms(samples)
            self._emit(self.on_vad, speech, energy)
            # ASR：仅在聆听态累积并分段识别。
            with self._lock:
                listening = self._listening
            if listening and self.asr is not None:
                self._asr_buffer.append(samples)
                buffer_seconds = sum(len(block) for block in self._asr_buffer) / self.sample_rate
                if speech:
                    self._silence_seconds = 0.0
                else:
                    self._silence_seconds += n / self.sample_rate
                if buffer_seconds - self._last_interim_seconds >= _INTERIM_INTERVAL_SECONDS:
                    interim = self.asr.accept(np.concatenate(self._asr_buffer))
                    if interim:
                        self._last_interim_seconds = buffer_seconds
                        self._emit(self.on_asr, interim, False)
                if self._silence_seconds >= _SILENCE_TIMEOUT_SECONDS and self._asr_buffer:
                    final = self.asr.finalize()
                    self._asr_buffer = []
                    self._silence_seconds = 0.0
                    self._last_interim_seconds = 0.0
                    if final:
                        self._emit(self.on_asr, final, True)


def _rms(samples: np.ndarray) -> float:
    arr = np.asarray(samples, dtype=np.float32)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(arr))))


def VAD_rms(samples: np.ndarray, threshold: float = 0.02) -> bool:
    """无 VAD 库时的能量兜底判定。"""
    return _rms(samples) >= threshold
