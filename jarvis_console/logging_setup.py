"""带密钥脱敏的统一日志入口。"""
from __future__ import annotations

import logging
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import LOGS_DIR

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]{8,}"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
)
_CONFIGURED = False
_CONFIG_LOCK = threading.Lock()


def redact(value: Any) -> str:
    """把日志文本中的常见密钥形式替换为不可逆占位符。"""
    text = str(value)
    text = _SECRET_PATTERNS[0].sub("sk-****(已脱敏)", text)
    text = _SECRET_PATTERNS[1].sub(r"\1****(已脱敏)", text)
    return _SECRET_PATTERNS[2].sub(r"\1****(已脱敏)", text)


class RedactingFormatter(logging.Formatter):
    """在最终 emit 前统一脱敏。"""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class MaxLevelFilter(logging.Filter):
    """限制处理器接收的最高日志等级。"""

    def __init__(self, maximum: int) -> None:
        super().__init__()
        self.maximum = maximum

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.maximum


def configure_logging(level: str = "INFO", logs_dir: Path = LOGS_DIR) -> None:
    """配置控制台、每日文件与错误文件处理器，重复调用安全。"""
    global _CONFIGURED
    with _CONFIG_LOCK:
        if _CONFIGURED:
            return
        logs_dir.mkdir(parents=True, exist_ok=True)
        numeric_level = getattr(logging, level.upper(), logging.INFO)
        root = logging.getLogger("jarvis_console")
        root.setLevel(numeric_level)
        root.propagate = False
        formatter = RedactingFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)

        daily_path = logs_dir / f"console-{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(daily_path, encoding="utf-8")
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)

        error_handler = logging.FileHandler(logs_dir / "errors.log", encoding="utf-8")
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)

        root.addHandler(console_handler)
        root.addHandler(file_handler)
        root.addHandler(error_handler)
        _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """返回项目 logger；调用者不得直接配置 logging。"""
    configure_logging()
    normalized = name if name.startswith("jarvis_console") else f"jarvis_console.{name}"
    return logging.getLogger(normalized)
