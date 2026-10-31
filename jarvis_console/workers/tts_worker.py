"""edge-tts 合成与播放工作线程。"""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QThread, Signal, Slot

from ..speech.tts import TTSBackend, speak


class TTSWorker(QThread):
    """在 UI 线程外执行网络合成和音频播放。"""

    sig_finished = Signal(dict)
    sig_error = Signal(dict)

    def __init__(
        self,
        text: str,
        *,
        voice: str = "en-GB-SoniaNeural",
        summarizer: Callable[[str], str] | None = None,
        threshold: int = 200,
        summary_max_chars: int = 80,
        backend: TTSBackend | None = None,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.text = text
        self.voice = voice
        self.summarizer = summarizer
        self.threshold = threshold
        self.summary_max_chars = summary_max_chars
        self.backend = backend

    @Slot()
    def run(self) -> None:
        try:
            spoken = speak(
                self.text,
                voice=self.voice,
                summarizer=self.summarizer,
                threshold=self.threshold,
                summary_max_chars=self.summary_max_chars,
                backend=self.backend,
            )
            self.sig_finished.emit({"ok": True, "spoken_text": spoken})
        except Exception as exc:
            self.sig_error.emit({
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            })
