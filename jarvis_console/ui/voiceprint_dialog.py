"""声纹注册对话框：采集若干段主人语音并写入 ~/.jarvis/voiceprint.npy。

- 录制只用 sounddevice（原始采音），不依赖唤醒/ASR 模型，离线可用。
- 声纹库（resemblyzer）缺失时优雅降级为提示，不崩溃。
- 采集 3 段、每段约 3 秒，取均值嵌入以稳健（对齐 voiceprint.Voiceprint.enroll 约定）。
"""
from __future__ import annotations

import threading

import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ..core.errors import DependencyMissing
from ..logging_setup import get_logger
from ..speech.voiceprint import Voiceprint

log = get_logger(__name__)

SAMPLE_RATE = 16000
SEGMENT_SECONDS = 3
SEGMENT_COUNT = 3


class MicCaptureWorker(QThread):
    """在子线程录制固定时长的单声道音频并返回 float32 数组。"""

    sig_done = Signal(object)  # np.ndarray
    sig_error = Signal(str)
    sig_tick = Signal(int)  # 剩余百分比进度

    def __init__(self, duration_seconds: int = SEGMENT_SECONDS, parent: object | None = None) -> None:
        super().__init__(parent)
        self.duration = duration_seconds

    def run(self) -> None:
        try:
            import sounddevice as sd  # type: ignore
        except ImportError as exc:
            self.sig_error.emit(str(DependencyMissing("未安装 sounddevice，无法录制声纹。")))
            return
        frames: list[np.ndarray] = []
        stop_event = threading.Event()
        total = int(self.duration * SAMPLE_RATE)
        captured = 0

        def callback(indata: np.ndarray, _frames: int, _info: object, _status: object) -> None:
            nonlocal captured
            chunk = np.copy(indata[:, 0]).astype(np.float32)
            frames.append(chunk)
            captured += chunk.size
            self.sig_tick.emit(int(min(100, captured / total * 100)))
            if captured >= total:
                stop_event.set()

        try:
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=480, callback=callback)
            with stream:
                stop_event.wait(timeout=self.duration + 2)
        except Exception as exc:  # noqa: BLE001 - 无麦克风设备等
            self.sig_error.emit(f"麦克风打开失败：{exc}")
            return
        if not frames:
            self.sig_error.emit("未采集到音频。")
            return
        self.sig_done.emit(np.concatenate(frames).astype(np.float32))


class EnrollWorker(QThread):
    """用多段样本注册声纹。"""

    sig_done = Signal(dict)
    sig_error = Signal(str)

    def __init__(self, samples: list[np.ndarray], parent: object | None = None) -> None:
        super().__init__(parent)
        self.samples = samples

    def run(self) -> None:
        try:
            result = Voiceprint().enroll(self.samples, SAMPLE_RATE)
        except DependencyMissing as exc:
            self.sig_error.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.sig_error.emit(f"声纹注册失败：{exc}")
            return
        self.sig_done.emit(result)


class VoiceprintEnrollDialog(QDialog):
    """引导用户录 3 段语音完成主人声纹注册。"""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("声纹注册")
        self.setMinimumWidth(380)
        self.samples: list[np.ndarray] = []
        self._capture: MicCaptureWorker | None = None
        self._enroll: EnrollWorker | None = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        self.status = QLabel("请在安静环境朗读任意句子。点击开始录制第 1 段。")
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.record_button = QPushButton("开始录制")
        self.record_button.clicked.connect(self._start_segment)
        layout.addWidget(self.record_button)
        self.clear_button = QPushButton("清除已注册声纹")
        self.clear_button.clicked.connect(self._clear)
        layout.addWidget(self.clear_button)
        self.done_button = QPushButton("完成")
        self.done_button.setEnabled(False)
        self.done_button.clicked.connect(self.accept)
        layout.addWidget(self.done_button)

    def _start_segment(self) -> None:
        if len(self.samples) >= SEGMENT_COUNT:
            return
        self.record_button.setEnabled(False)
        self.status.setText(f"录制中…（第 {len(self.samples) + 1}/{SEGMENT_COUNT} 段，请说话）")
        self.progress.setValue(0)
        self._capture = MicCaptureWorker(SEGMENT_SECONDS, self)
        self._capture.sig_done.connect(self._on_segment)
        self._capture.sig_error.connect(self._on_error)
        self._capture.sig_tick.connect(self.progress.setValue)
        self._capture.finished.connect(self._capture.deleteLater)
        self._capture.start()

    def _on_segment(self, samples: np.ndarray) -> None:
        self.samples.append(samples)
        if len(self.samples) < SEGMENT_COUNT:
            self.status.setText(f"已录 {len(self.samples)}/{SEGMENT_COUNT} 段，点击继续。")
            self.record_button.setEnabled(True)
        else:
            self.status.setText("已采集 3 段，正在生成声纹…")
            self._enroll_now()

    def _enroll_now(self) -> None:
        self._enroll = EnrollWorker(list(self.samples), self)
        self._enroll.sig_done.connect(self._on_enrolled)
        self._enroll.sig_error.connect(self._on_error)
        self._enroll.finished.connect(self._enroll.deleteLater)
        self._enroll.start()

    def _on_enrolled(self, result: dict) -> None:
        if result.get("status") == "enrolled":
            self.status.setText(f"声纹注册成功（{result.get('segments')} 段，维度 {result.get('embedding_dim')}）。")
            self.done_button.setEnabled(True)
            self.record_button.setEnabled(False)
        else:
            self.status.setText(f"注册未完成：{result.get('error', '未知')}")
            self.record_button.setEnabled(True)

    def _on_error(self, message: str) -> None:
        self.status.setText(message)
        self.record_button.setEnabled(True)
        QMessageBox.warning(self, "声纹注册", message)

    def _clear(self) -> None:
        Voiceprint().clear()
        self.samples = []
        self.status.setText("已清除已注册声纹，可重新录制。")
        self.done_button.setEnabled(False)
        self.record_button.setEnabled(True)
