"""webrtcvad 封装：能量 / 语音活动检测，供 barge-in 打断与免唤醒追问。"""
from __future__ import annotations

from typing import Any

import numpy as np

from ..core.errors import DependencyMissing
from ..logging_setup import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 16000
# webrtcvad 支持的帧时长（毫秒）与对应样本数（16k）。
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480
# 默认模式 3（最激进，最容易判定为语音），用于打断灵敏度。
DEFAULT_AGGRESSIVENESS = 3


class VAD:
    """对 16k 单声道 float32 音频做语音活动检测。"""

    def __init__(self, aggressiveness: int = DEFAULT_AGGRESSIVENESS, sample_rate: int = SAMPLE_RATE) -> None:
        self.aggressiveness = aggressiveness
        self.sample_rate = sample_rate
        self._vad = self._load()
        # 缓存不足一帧的残余样本，保证帧对齐。
        self._residual = np.zeros(0, dtype=np.float32)

    @staticmethod
    def _load() -> Any:
        try:
            import webrtcvad  # type: ignore
        except ImportError as exc:
            raise DependencyMissing(
                "未安装 webrtcvad，打断检测未启用；请执行 pip install webrtcvad>=3.0.4。"
            ) from exc
        return webrtcvad.Vad(DEFAULT_AGGRESSIVENESS)

    def is_available(self) -> bool:
        """webrtcvad 是否已安装。"""
        try:
            self._load()
            return True
        except DependencyMissing:
            return False

    @staticmethod
    def rms_energy(samples: np.ndarray) -> float:
        """计算一段音频的 RMS 能量（0..1 量级）。"""
        arr = np.asarray(samples, dtype=np.float32)
        if arr.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(arr))))

    def is_speech(self, samples: np.ndarray) -> bool:
        """判断一段音频是否包含语音（按 30ms 帧，多数帧命中即判为真）。"""
        if self._vad is None:
            return False
        arr = np.asarray(samples, dtype=np.float32)
        data = np.concatenate([self._residual, arr])
        if data.size < FRAME_SAMPLES:
            self._residual = data
            return False
        usable = (data.size // FRAME_SAMPLES) * FRAME_SAMPLES
        frames = data[:usable].reshape(-1, FRAME_SAMPLES)
        self._residual = data[usable:]
        speech_frames = 0
        total = 0
        for frame in frames:
            total += 1
            pcm16 = (np.clip(frame, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            try:
                if self._vad.is_speech(pcm16, self.sample_rate):
                    speech_frames += 1
            except Exception:  # noqa: BLE001 - 单帧异常忽略
                continue
        if total == 0:
            return False
        return speech_frames / total >= 0.5


def vad_status() -> dict[str, object]:
    """返回 VAD 能力状态。"""
    try:
        import webrtcvad  # type: ignore
    except ImportError:
        return {"available": False, "message": "未安装 webrtcvad，打断检测未启用。"}
    return {"available": True, "message": "VAD 就绪。"}
