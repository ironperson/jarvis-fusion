"""QApplication 组装与依赖降级入口。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

from .config import get_config, get_secret, has_secret
from .core.llm_client import DeepSeekClient, EngineRouter, MockLLMClient, OllamaClient
from .core.memory import MemoryStore
from .core.notestore import NoteStore, mock_notestore
from .core.types import LLMResponse
from .logging_setup import configure_logging, get_logger
from .paths import HOME_JARVIS, MEMORY_DB, bootstrap_paths

log = get_logger(__name__)


def _is_mock() -> bool:
    return get_config().llm.mode.lower() == "mock"


def _build_router() -> EngineRouter:
    config = get_config()
    if _is_mock():
        return EngineRouter(MockLLMClient(), None, fail_threshold=1)
    # 融合版：从 console.yaml 的 6 家服务商配置构造 N 路路由器；缺 Key 的服务商标记
    # 不可用（ProviderSelector 仍展示但切到后会自动降级），不阻断主链路。
    try:
        return EngineRouter.from_providers(
            config.providers,
            timeout=config.llm.timeout_seconds,
            fail_threshold=config.llm.fail_threshold,
        )
    except Exception as exc:  # noqa: BLE001 - 配置异常时回退到双引擎，保证可启动
        log.warning("多服务商路由装配失败，回退到 DeepSeek+Ollama 双引擎：%s", exc)
        primary = DeepSeekClient(
            api_key=get_secret("DEEPSEEK_API_KEY"),
            base_url=config.llm.deepseek_base_url,
            model=config.llm.deepseek_model,
            disable_thinking=config.llm.disable_thinking,
        )
        fallback = OllamaClient(config.llm.ollama_base_url, config.llm.ollama_model)
        return EngineRouter(primary, fallback, config.llm.fail_threshold)


def _summarizer(router: EngineRouter, timeout: int) -> Callable[[str], str]:
    def summarize(text: str) -> str:
        response: LLMResponse = router.chat(
            [
                {"role": "system", "content": "请把正文压缩成不超过80个汉字的可朗读中文结论，只输出摘要。"},
                {"role": "user", "content": text},
            ],
            [],
            timeout=timeout,
        )
        return response.content
    return summarize


def _build_audio(config: Any) -> Any:
    """构造离线语音链路（唤醒 + ASR + VAD）；模型/库缺失时逐项降级，全部缺失则不启用语音。

    返回 ``AudioService`` 或 ``None``（纯文字驱动）。麦克风依赖 sounddevice，未安装也降级。
    """
    from .speech.asr import SenseVoiceStream
    from .speech.audio_service import AudioService
    from .speech.vad import VAD
    from .speech.wake import WakeEngine

    def _try_build(fn: Callable[[], Any], label: str) -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - 缺库/缺模型，优雅降级
            log.warning("语音组件 %s 不可用（优雅降级）：%s", label, exc)
            return None

    wake = _try_build(WakeEngine, "WakeEngine")
    asr = _try_build(SenseVoiceStream, "SenseVoiceStream")
    vad = _try_build(VAD, "VAD")

    if not (wake or asr or vad):
        log.info("唤醒/ASR/VAD 均不可用，语音链路关闭（纯文字驱动 HUD 四态）。")
        return None
    audio = AudioService(wake=wake, asr=asr, vad=vad)
    if not audio.is_available():
        log.warning("sounddevice 未安装，麦克风不可用，语音链路关闭。")
        return None
    return audio


def run() -> int:
    """启动总控台；缺 PySide6 时输出可执行的安装指引而非 traceback。"""
    bootstrap_paths()
    config = get_config(reload=True)
    configure_logging(config.log_level)
    log = get_logger(__name__)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        from .tools import REGISTRY, configure_tools
        from .tools.filetools import authorize_path
        from .ui.main_window import MainWindow
        from .ui.theme import DARK_QSS
        from .workers.agent_worker import AgentWorker
        from .speech.voiceprint import Voiceprint
        from .workers.status_worker import StatusWorker
        from .workers.reminder_worker import ReminderWorker
    except ImportError as exc:
        sys.stderr.write(
            "总控台 UI 依赖未就绪："
            f"{exc}\n请执行：.venv/Scripts/python.exe -m pip install -i "
            "https://mirrors.aliyun.com/pypi/simple --timeout 15 --no-cache-dir PySide6\n"
        )
        return 2

    configure_tools()
    mock_root = Path(os.environ.get("JARVIS_MOCK_DIR", str(HOME_JARVIS / "mock_workspace"))).resolve()
    if _is_mock():
        mock_root.mkdir(parents=True, exist_ok=True)
        memory = MemoryStore(mock_root / "memory.db", ollama_base_url="http://127.0.0.1:1")
        note_store = mock_notestore(mock_root / "knowledge")
    else:
        memory = MemoryStore(MEMORY_DB, config.llm.ollama_base_url)
        note_store = NoteStore()
    router = _build_router()

    # —— 融合版附加组件（优雅降级，缺模型/库则各自退场）——
    audio = _build_audio(config)
    status_worker = StatusWorker()
    reminder_worker = ReminderWorker()
    voiceprint = Voiceprint()

    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("小王老师 · 总控台")
    application.setStyleSheet(DARK_QSS)

    try:
        initial_count = note_store.count()
    except Exception as exc:
        log.warning("知识库计数不可用：%s", exc)
        initial_count = 0

    def worker_factory(text: str, trust_mode: bool, max_rounds: int) -> AgentWorker:
        return AgentWorker(
            REGISTRY,
            router,
            memory,
            text,
            trust_mode=trust_mode,
            max_rounds=max_rounds,
            timeout=config.llm.timeout_seconds,
            confirmation_timeout=config.safety.confirmation_timeout_seconds,
        )

    window = MainWindow(
        worker_factory=worker_factory,
        revoke_fn=note_store.delete,
        kb_count_fn=note_store.count,
        path_authorizer=authorize_path,
        summarizer=_summarizer(router, config.llm.timeout_seconds),
        deepseek_configured=has_secret("DEEPSEEK_API_KEY"),
        speech_enabled=config.ui.speech_enabled,
        default_max_rounds=config.ui.max_rounds,
        voice=config.ui.voice,
        speech_threshold=config.ui.long_speech_threshold,
        summary_max_chars=config.ui.summary_max_chars,
        initial_kb_count=initial_count,
        router=router,
        audio=audio,
        voiceprint=voiceprint,
        status_worker=status_worker,
        reminder_worker=reminder_worker,
    )
    window.show()
    if not has_secret("DEEPSEEK_API_KEY") and not _is_mock():
        QMessageBox.information(
            window,
            "DeepSeek Key 未配置",
            "没找到 DEEPSEEK_API_KEY。窗口与本地工具仍可用，并会尝试 Ollama。\n\n"
            "推荐写入：C:\\Users\\<你的用户名>\\.jarvis\\.env\n"
            "内容：DEEPSEEK_API_KEY=你的Key\n\n配好后重新发送消息即可；Key 不会显示在 UI 或日志里。",
        )
    return int(application.exec())
