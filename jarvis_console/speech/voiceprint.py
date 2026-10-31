"""本地轻量声纹：注册（主人）与校验（只回应主人）。

使用 ``resemblyzer`` 的 d-vector 余弦比对，本地推理、无密钥、体积小。
注册时采 ≥3 段取均值嵌入，校验余弦 ≥阈值 放行（默认 0.85，可调）。
嵌入持久化到 ``~/.jarvis/voiceprint.npy``；缺失库/嵌入时优雅降级。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ..core.errors import DependencyMissing
from ..logging_setup import get_logger
from ..paths import VOICEPRINT_PATH

log = get_logger(__name__)

DEFAULT_THRESHOLD = 0.85
EMBED_DIM = 256


def _import_resembedlyzer() -> Any:
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
    except ImportError as exc:
        raise DependencyMissing(
            "未安装 resemblyzer，声纹功能未启用；请执行 pip install resemblyzer>=0.1.1。"
        ) from exc
    return VoiceEncoder, preprocess_wav


def _to_wav_array(clip: np.ndarray, sample_rate: int) -> np.ndarray:
    """把任意采样率的单声道 float32 信号重采样到 16k 供 resemblyzer 使用。"""
    samples = np.asarray(clip, dtype=np.float32)
    if sample_rate != 16000:
        try:
            import resampy  # type: ignore

            samples = resampy.resample(samples, sample_rate, 16000)
        except ImportError:
            # 无 resampy 时退化为简单线性重采样（声纹精度略降，但不阻塞主链路）。
            ratio = 16000 / max(1, sample_rate)
            indices = np.linspace(0, len(samples) - 1, int(len(samples) * ratio))
            samples = np.interp(indices, np.arange(len(samples)), samples).astype(np.float32)
    return samples


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个归一化友好的余弦相似度（未归一化向量也安全）。"""
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


class Voiceprint:
    """主人声纹注册与校验。"""

    def __init__(self, store_path: Path | str = VOICEPRINT_PATH, threshold: float = DEFAULT_THRESHOLD) -> None:
        self.store_path = Path(store_path)
        self.threshold = threshold
        self._encoder = None

    def is_available(self) -> bool:
        """resemblyzer 是否已安装（决定声纹功能是否可用）。"""
        try:
            _import_resembedlyzer()
            return True
        except DependencyMissing:
            return False

    def has_enrollment(self) -> bool:
        """是否已存在主人声纹嵌入。"""
        return self.store_path.is_file()

    def _embed(self, clip: np.ndarray, sample_rate: int) -> np.ndarray:
        if self._encoder is None:
            VoiceEncoder, preprocess_wav = _import_resembedlyzer()
            self._encoder = VoiceEncoder()
        wav = _to_wav_array(clip, sample_rate)
        wav = preprocess_wav(wav, source_sr=16000)
        return np.asarray(self._encoder.embed_utterance(wav), dtype=np.float32)

    def enroll(self, samples: list[np.ndarray], sample_rate: int = 16000) -> dict[str, Any]:
        """用 ≥1 段音频注册主人声纹；多段取均值嵌入以稳健。

        返回 VoiceprintEnroll 风格字典：owner_id / embedding 长度 / embedding_path / status。
        """
        if not samples:
            return {"owner_id": "owner", "status": "failed", "error": "未提供声纹样本"}
        embeddings = [self._embed(clip, sample_rate) for clip in samples if np.any(clip)]
        if not embeddings:
            return {"owner_id": "owner", "status": "failed", "error": "声纹样本为空"}
        mean_embedding = np.mean(np.stack(embeddings), axis=0).astype(np.float32)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(self.store_path, mean_embedding)
        return {
            "owner_id": "owner",
            "embedding_dim": int(mean_embedding.shape[0]),
            "embedding_path": str(self.store_path),
            "segments": len(embeddings),
            "status": "enrolled",
        }

    def verify(self, clip: np.ndarray, sample_rate: int = 16000) -> dict[str, Any]:
        """校验一段音频是否来自主人；返回 VoiceprintVerify 风格字典 match/score。"""
        if not self.has_enrollment():
            # 未注册声纹：按 PRD，可在设置里关闭声纹降级为“任意人都响应”，由调用方裁决。
            return {"match": True, "score": 1.0, "reason": "no_enrollment"}
        try:
            enrolled = np.load(self.store_path).astype(np.float32).ravel()
        except Exception as exc:  # noqa: BLE001
            log.warning("读取声纹嵌入失败：%s", exc)
            return {"match": False, "score": 0.0, "reason": "load_error"}
        probe = self._embed(clip, sample_rate)
        score = cosine_similarity(enrolled, probe)
        return {"match": bool(score >= self.threshold), "score": float(score)}

    def clear(self) -> bool:
        """清空已注册声纹（用户可在设置里重注册/关闭）。"""
        if self.store_path.is_file():
            self.store_path.unlink()
        return not self.store_path.is_file()


def voiceprint_status() -> dict[str, object]:
    """返回声纹能力状态。"""
    try:
        _import_resembedlyzer()
    except DependencyMissing:
        return {"available": False, "enrolled": False, "message": "未安装 resemblyzer，声纹未启用。"}
    return {
        "available": True,
        "enrolled": VOICEPRINT_PATH.is_file(),
        "message": "声纹就绪。" if VOICEPRINT_PATH.is_file() else "声纹可用，但尚未注册主人。",
    }
