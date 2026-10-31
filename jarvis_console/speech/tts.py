"""语音输出的文本摘要规则、TTS 后端抽象与播放。

后端可插拔：默认 ``EdgeTTSBackend``（英式 en-GB，符合 R1），可选合规 ``PiperBackend``。
``prepare_speech_text`` 摘要逻辑保持不变（长文只播 ≤80 字）；播放失败仅提示、不阻断主链路。
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from ..core.errors import DependencyMissing
from ..logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_EN_GB_VOICE = "en-GB-SoniaNeural"


def _plain_text(text: str) -> str:
    cleaned = re.sub(r"```[\s\S]*?```", "代码内容。", text)
    cleaned = re.sub(r"https?://\S+", "链接", cleaned)
    cleaned = re.sub(r"[#>*_`|\[\]()-]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def prepare_speech_text(
    text: str,
    summarizer: Callable[[str], str] | None = None,
    threshold: int = 200,
    summary_max_chars: int = 80,
) -> str:
    """短文播全文；长文让当前引擎摘要，失败则播前 200 字。"""
    plain = _plain_text(text)
    if len(plain) <= threshold:
        return plain
    if summarizer is not None:
        try:
            summary = _plain_text(summarizer(plain)).strip()
            if summary:
                return summary[:summary_max_chars]
        except Exception:
            pass
    return plain[:threshold]


async def _save_audio(text: str, path: Path, voice: str) -> None:
    import edge_tts

    communicator = edge_tts.Communicate(text=text, voice=voice)
    await communicator.save(str(path))


def _play_windows(path: Path) -> None:
    escaped = str(path).replace("'", "''")
    script = (
        "Add-Type -AssemblyName PresentationCore; "
        "$p=New-Object System.Windows.Media.MediaPlayer; "
        f"$p.Open([Uri]'{escaped}'); $p.Play(); "
        "while(-not $p.NaturalDuration.HasTimeSpan){Start-Sleep -Milliseconds 100}; "
        "$ms=$p.NaturalDuration.TimeSpan.TotalMilliseconds; Start-Sleep -Milliseconds ([int]$ms+200); "
        "$p.Close()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        timeout=180,
    )


def _edge_speak(text: str, voice: str) -> str:
    """edge-tts 合成并播放；返回实际播报文本（已是摘要后的内容）。"""
    with tempfile.TemporaryDirectory(prefix="jarvis_tts_") as temporary_dir:
        audio_path = Path(temporary_dir) / "speech.mp3"
        asyncio.run(_save_audio(text, audio_path, voice))
        if os.name == "nt":
            _play_windows(audio_path)
        else:
            player = os.environ.get("JARVIS_AUDIO_PLAYER", "").strip()
            if not player:
                raise RuntimeError("非 Windows 环境未配置 JARVIS_AUDIO_PLAYER，已完成合成但无法播放")
            subprocess.run([player, str(audio_path)], check=True, timeout=180)
    return text


class TTSBackend(ABC):
    """TTS 后端抽象；所有后端都负责“合成 + 播放 + 返回播报文本”。"""

    name: str = "base"

    @abstractmethod
    def speak(self, text: str) -> str:
        """合成并播放 ``text``，返回实际播报文本。"""

    def stop(self) -> None:
        """可选：打断当前播放。默认无操作。"""


class EdgeTTSBackend(TTSBackend):
    """edge-tts 后端；默认英式嗓音（en-GB），符合 R1 的兜底方案。"""

    name = "edge"

    def __init__(self, voice: str = DEFAULT_EN_GB_VOICE) -> None:
        self.voice = voice

    def speak(self, text: str) -> str:
        return _edge_speak(text, self.voice)


class PiperBackend(TTSBackend):
    """可选合规 Piper 英式嗓音后端；未安装 piper-tts 时优雅降级为不可用。"""

    name = "piper"

    def __init__(self, model_path: str, voice: str = "en_GB") -> None:
        self.model_path = model_path
        self.voice = voice
        self._piper = self._load()

    @staticmethod
    def _load() -> object | None:
        try:
            from piper import PiperVoice  # type: ignore
        except ImportError:
            return None
        return PiperVoice

    def is_available(self) -> bool:
        return self._piper is not None and Path(self.model_path).is_file()

    def speak(self, text: str) -> str:
        if not self.is_available():
            raise DependencyMissing("未安装 piper-tts 或模型缺失；请改用 edge 后端。")
        with tempfile.TemporaryDirectory(prefix="jarvis_piper_") as temporary_dir:
            audio_path = Path(temporary_dir) / "speech.wav"
            voice = self._piper.load(self.model_path)  # type: ignore[union-attr]
            with open(audio_path, "wb") as file:
                voice.synthesize(text, file)
            if os.name == "nt":
                _play_windows(audio_path)
            else:
                player = os.environ.get("JARVIS_AUDIO_PLAYER", "").strip()
                if not player:
                    raise RuntimeError("非 Windows 环境未配置 JARVIS_AUDIO_PLAYER")
                subprocess.run([player, str(audio_path)], check=True, timeout=180)
        return text


def get_backend(name: str = "edge", voice: str = DEFAULT_EN_GB_VOICE, piper_model: str = "") -> TTSBackend:
    """按名称构造 TTS 后端；未知或不可用的后端回退到 edge。"""
    if name == "piper":
        backend: TTSBackend = PiperBackend(piper_model or "", voice=voice)
        if backend.is_available():
            return backend
        log.warning("Piper 后端不可用，回退到 edge-tts。")
    return EdgeTTSBackend(voice=voice)


def speak(
    text: str,
    *,
    voice: str = DEFAULT_EN_GB_VOICE,
    summarizer: Callable[[str], str] | None = None,
    threshold: int = 200,
    summary_max_chars: int = 80,
    backend: TTSBackend | None = None,
) -> str:
    """合成并同步播放语音；调用方必须位于 TTS 工作线程。

    ``backend`` 优先；为空时按 voice 构造默认 edge 后端。
    """
    speech_text = prepare_speech_text(text, summarizer, threshold, summary_max_chars)
    if not speech_text:
        return "没有可播报的文本"
    selected = backend or EdgeTTSBackend(voice=voice)
    return selected.speak(speech_text)
