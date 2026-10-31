"""PyInstaller 打包入口（绝对导入，避免 __main__.py 的相对导入在脚本模式下失败）。

运行方式：由 jarvis.spec 作为 Analysis 的主脚本收集，最终产物 Jarvis.exe 即执行本文件。
"""
from __future__ import annotations

from jarvis_console.app import run


if __name__ == "__main__":
    raise SystemExit(run())
