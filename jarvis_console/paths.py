"""总控台统一路径常量与导入引导。"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
PIPELINE_ROOT = REPO_ROOT / "pipeline"
HOME_JARVIS = Path.home() / ".jarvis"
HOME_SSH_DIR = Path.home() / ".ssh"
HOME_JARVIS_ENV = HOME_JARVIS / ".env"
REPO_ENV = REPO_ROOT / ".env"
CONFIG_FILE = PACKAGE_ROOT / "config" / "console.yaml"
FIXTURES_DIR = PACKAGE_ROOT / "fixtures"
LOGS_DIR = PACKAGE_ROOT / "logs"
KNOWLEDGE_DIR = Path("D:/Desktop/MyKnowledge")
KNOWLEDGE_DB = KNOWLEDGE_DIR / "pipeline.db"
MEMORY_DB = HOME_JARVIS / "memory.db"

# —— 融合版语音模型路径（收敛到总控台工作目录，避免散落引用 jarvis/config.py）——
MODELS_DIR = PACKAGE_ROOT / "models"
WAKE_MODEL = MODELS_DIR / "wakeword"
ASR_MODEL = MODELS_DIR / "asr" / "sensevoice"
PIPER_MODEL = MODELS_DIR / "piper"
VOICEPRINT_PATH = HOME_JARVIS / "voiceprint.npy"
REMINDERS_PATH = HOME_JARVIS / "reminders.json"


def bootstrap_paths() -> None:
    """把仓库根与 pipeline 根加入模块搜索路径，不改变工作目录。"""
    for path in (REPO_ROOT, PIPELINE_ROOT):
        path_text = str(path)
        if path_text not in sys.path:
            sys.path.insert(0, path_text)
    HOME_JARVIS.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _windows_known_folder(csidl: int) -> Path | None:
    """通过 Windows Shell API 读取已重定向的用户目录。"""
    if os.name != "nt":
        return None
    buffer = ctypes.create_unicode_buffer(32768)
    try:
        result = ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer)
    except (AttributeError, OSError):
        return None
    if result != 0 or not buffer.value:
        return None
    return Path(buffer.value).expanduser().resolve()


def system_desktop() -> Path:
    """动态返回系统桌面，兼容桌面被重定向到其他磁盘。"""
    detected = _windows_known_folder(0x0010)
    if detected is not None:
        return detected
    configured = os.environ.get("JARVIS_DESKTOP", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / "Desktop").resolve()


def system_documents() -> Path:
    """动态返回用户文档目录。"""
    return _windows_known_folder(0x0005) or (Path.home() / "Documents").resolve()


def system_downloads() -> Path:
    """返回用户下载目录；旧 Shell API 无 Downloads CSIDL，使用用户目录拼接。"""
    return (Path.home() / "Downloads").resolve()


bootstrap_paths()
