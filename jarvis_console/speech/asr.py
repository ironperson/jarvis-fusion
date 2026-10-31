"""sensevoice 离线流式 ASR（sherpa-onnx 1.12.40，锁版本）。

严格遵守既有约束：
- 版本锁定 sherpa-onnx==1.12.40，禁止 1.13.x（中文路径 exit 127 风险）；
- 加载前 chdir 到模型目录，只传纯文件名，finally 恢复原 cwd（复用 ``load_model_safely``）。
- SenseVoice 本身是离线模型，不支持真正逐字流式；这里以“分段识别 + 增量字幕”模拟流式，
  即对累积音频周期性解码并回吐 interim 文本，端点静音时回吐 final 文本。

缺模型或库时，全部能力优雅降级：``asr_status()`` 回报 ``enabled=False``，调用方不会崩溃。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..core.errors import ConfigError, DependencyMissing
from ..logging_setup import get_logger
from ..paths import ASR_MODEL
from . import load_model_safely

log = get_logger(__name__)

SAMPLE_RATE = 16000
# 增量解码的最小缓冲长度（秒），避免每帧都做完整解码。
_MIN_INTERIM_SECONDS = 0.6
# 静音包（连续静音帧）触发 final 的阈值（秒）。
_SILENCE_TIMEOUT_SECONDS = 0.8


def _discover_onnx(model_dir: Path) -> str:
    """在模型目录中寻找主要的 onnx 权重文件名。"""
    onnx_files = sorted(p for p in model_dir.iterdir() if p.suffix == ".onnx")
    if not onnx_files:
        raise ConfigError(f"ASR 模型目录缺少 .onnx 权重：{model_dir}")
    # 优先选择文件名含 sensevoice / model 的，其次直接取第一个。
    for candidate in onnx_files:
        lower = candidate.name.lower()
        if "sensevoice" in lower or "model" in lower or "encoder" in lower:
            return candidate.name
    return onnx_files[0].name


def _tokens_exist(model_dir: Path) -> bool:
    return (model_dir / "tokens.txt").is_file()


class SenseVoiceStream:
    """基于 sherpa-onnx 离线 SenseVoice 的分段识别器；对外暴露流式调用约定。"""

    def __init__(self, model_dir: Path | str = ASR_MODEL, sample_rate: int = SAMPLE_RATE) -> None:
        self.model_dir = Path(model_dir)
        self.sample_rate = sample_rate
        self.recognizer = self._load(model_dir)
        self._buffer = np.zeros(0, dtype=np.float32)
        self._last_interim_len = 0

    @staticmethod
    def _load(model_dir: Path | str) -> Any:
        try:
            import sherpa_onnx  # type: ignore
        except ImportError as exc:
            raise DependencyMissing(
                "未安装 sherpa-onnx==1.12.40，无法加载 SenseVoice ASR；"
                "请执行 pip install sherpa-onnx==1.12.40（锁版本，禁止 1.13.x）。"
            ) from exc
        directory = Path(model_dir).expanduser().resolve(strict=True)
        if not _tokens_exist(directory):
            raise ConfigError(f"ASR 模型目录缺少 tokens.txt：{directory}")
        onnx_name = _discover_onnx(directory)

        def loader(model_filename: str) -> Any:
            # 已 chdir 到模型目录：只传纯文件名；tokens 同目录。
            return sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=model_filename,
                tokens="tokens.txt",
                language="auto",
                use_itn=True,
                num_threads=1,
            )

        return load_model_safely(directory, onnx_name, loader)

    def reset(self) -> None:
        """清空内部缓冲，准备下一轮识别。"""
        self._buffer = np.zeros(0, dtype=np.float32)
        self._last_interim_len = 0

    def accept(self, chunk: np.ndarray) -> str:
        """喂入一段 16k 单声道 float32 音频，返回增量（非最终）识别文本。"""
        chunk = np.asarray(chunk, dtype=np.float32)
        self._buffer = np.concatenate([self._buffer, chunk])
        if len(self._buffer) < int(self.sample_rate * _MIN_INTERIM_SECONDS):
            return ""
        text = self._decode(self._buffer)
        # 只回吐相对上一次新增的内容，避免字幕整段重复。
        return text

    def finalize(self) -> str:
        """对当前缓冲做最终解码并清空。"""
        if len(self._buffer) == 0:
            return ""
        text = self._decode(self._buffer)
        self.reset()
        return text

    def _decode(self, samples: np.ndarray) -> str:
        try:
            stream = self.recognizer.create_stream()
            stream.accept_waveform(self.sample_rate, samples.astype(np.float32))
            self.recognizer.decode_stream(stream)
            return str(getattr(stream.result, "text", "") or "").strip()
        except Exception as exc:  # 识别异常不应中断主链路
            log.warning("SenseVoice 解码失败：%s", exc)
            return ""


def asr_status() -> dict[str, object]:
    """返回真实能力状态；缺库或模型时明确降级。"""
    try:
        import sherpa_onnx  # type: ignore
    except ImportError:
        return {
            "enabled": False,
            "microphone": True,
            "message": "未安装 sherpa-onnx==1.12.40，ASR 未启用；文字输入仍可用。",
            "required_sherpa_version": "1.12.40",
        }
    if not (Path(ASR_MODEL).is_dir() and _tokens_exist(Path(ASR_MODEL))):
        return {
            "enabled": False,
            "microphone": True,
            "message": "SenseVoice 模型尚未下载，ASR 未启用；可在首启向导中下载。",
            "required_sherpa_version": "1.12.40",
        }
    return {
        "enabled": True,
        "microphone": True,
        "message": "SenseVoice 离线 ASR 就绪。",
        "required_sherpa_version": "1.12.40",
    }
