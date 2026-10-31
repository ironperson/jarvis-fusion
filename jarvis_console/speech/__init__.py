"""语音能力包。

注意加载顺序：``load_model_safely`` 必须在导入子模块之前定义，因为 ``asr.py`` / ``wake.py``
在顶层执行 ``from . import load_model_safely``（此时 ``speech`` 包正在初始化，只有先定义好该符号，
子模块才能成功解析）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

# —— 共享的模型加载纪律（中文路径 / sherpa-onnx 1.12.40）——
# 所有 C++ 层（sherpa-onnx）加载都必须先 chdir 到模型目录、只传纯文件名、finally 恢复 cwd，
# 以规避中文路径下 exit 127 的风险。复用点：speech/asr.py、speech/wake.py。
def load_model_safely(
    model_dir: Path | str,
    model_filename: str,
    loader: Callable[[str], Any],
) -> Any:
    """在 ``model_dir`` 内（chdir 临时切换）调用 ``loader(model_filename)`` 并返回结果。

    - 进入前记录当前工作目录，``finally`` 中无条件恢复，避免污染后续逻辑；
    - ``loader`` 只接收纯文件名（不接收绝对路径），由调用方保证模型文件就在 ``model_dir`` 下；
    - 中文路径场景必须把 cwd 切到模型目录，否则 sherpa-onnx 1.12.40 可能 exit 127。
    """
    directory = Path(model_dir).expanduser().resolve(strict=False)
    previous = os.getcwd()
    try:
        os.chdir(directory)
        return loader(model_filename)
    finally:
        try:
            os.chdir(previous)
        except OSError:
            # 极端情况下原 cwd 已不存在：尽力恢复，忽略失败。
            pass


from .asr import SenseVoiceStream, asr_status
from .audio_service import AudioService
from .tts import (
    EdgeTTSBackend,
    PiperBackend,
    TTSBackend,
    get_backend,
    prepare_speech_text,
    speak,
)
from .vad import VAD, vad_status
from .voiceprint import Voiceprint, voiceprint_status
from .wake import WakeEngine, wake_status

__all__ = [
    "prepare_speech_text",
    "speak",
    "SenseVoiceStream",
    "asr_status",
    "AudioService",
    "WakeEngine",
    "wake_status",
    "Voiceprint",
    "voiceprint_status",
    "VAD",
    "vad_status",
    "TTSBackend",
    "EdgeTTSBackend",
    "PiperBackend",
    "get_backend",
    "load_model_safely",
]
