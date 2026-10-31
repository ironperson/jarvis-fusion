"""允许通过 ``python -m jarvis_console`` 启动应用。"""
from __future__ import annotations

from .app import run


if __name__ == "__main__":
    raise SystemExit(run())
