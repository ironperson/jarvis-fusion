"""全息 HUD 主界面：QWebEngineView 承载 Three.js 页面 + QWebSocketServer 桥。

桥协议（对齐上游 GDDXX-Jarvis）：
- ws://127.0.0.1:8765；Python → HUD 推 HUDState JSON；HUD → Python 发 {type:"text"|"interrupt"|...}。
- 页面在 hud/index.html，脚本按 hud/js/boot.js 的 PARTS 顺序加载，后文件可用前文件变量。
- 依赖仅 Qt 原生（QWebSocketServer 随 PySide6 自带），无需额外 websockets 包。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl, Signal
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

from ..logging_setup import get_logger
from ..paths import PACKAGE_ROOT

log = get_logger(__name__)

HUD_DIR = PACKAGE_ROOT / "hud"
WS_PORT = 8765


class HUD(QWebEngineView):
    """承载四态界面与字幕的 Web 容器，并用内置 WS 服务与页面互通。"""

    # HUD → Python 的消息（已解析为 dict）。
    sig_message = Signal(dict)

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._client: QWebSocket | None = None
        self._server = QWebSocketServer("jarvis-hud", QWebSocketServer.SslMode.NonSecureMode)
        self._load_page()
        if self._server.listen(QHostAddress.LocalHost, WS_PORT):
            self._server.newConnection.connect(self._on_new_connection)
            log.info("HUD WebSocket 桥已监听 ws://127.0.0.1:%d", WS_PORT)
        else:
            log.warning("HUD WebSocket 桥启动失败：%s", self._server.errorString())

    def _load_page(self) -> None:
        index = HUD_DIR / "index.html"
        if index.is_file():
            self.setUrl(QUrl.fromLocalFile(str(index)))
        else:
            log.warning("HUD 页面缺失：%s", index)

    def _on_new_connection(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnected.disconnect()
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
        client = self._server.nextPendingConnection()
        if client is None:
            return
        self._client = client
        client.textMessageReceived.connect(self._on_text)
        client.disconnected.connect(self._on_disconnected)

    def _on_disconnected(self) -> None:
        if self.sender() is self._client:
            self._client = None

    def _on_text(self, message: str) -> None:
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return
        if isinstance(data, dict):
            self.sig_message.emit(data)

    # —— 推送接口（Python → HUD）——
    def push(self, state: dict[str, Any]) -> None:
        """向 HUD 推一份 HUDState 字典（四态/字幕/引擎/系统/演示/皮肤）。"""
        if self._client is not None:
            try:
                self._client.sendTextMessage(json.dumps(state, ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001
                log.debug("HUD 推送失败：%s", exc)

    def push_state(
        self,
        state: str,
        subtitle: str = "",
        engine: str = "",
        sys: dict[str, Any] | None = None,
        demo: str | None = None,
        skin: str | None = None,
    ) -> None:
        """便捷封装：组装 HUDState 并推送。"""
        self.push({
            "state": state,
            "subtitle": subtitle,
            "engine": engine,
            "sys": sys or {},
            "demo": demo,
            "skin": skin,
        })

    def update_sys(self, sys: dict[str, Any]) -> None:
        """仅更新系统状态（顶栏实时刷新，不切状态）。"""
        self.push({"state": "_sys", "sys": sys})

    def update_subtitle(self, text: str, is_final: bool = True) -> None:
        """仅更新字幕。"""
        self.push({"state": "_subtitle", "subtitle": text, "is_final": is_final})

    def set_demo(self, demo: str | None) -> None:
        """切换 3D 演示场景。"""
        self.push({"state": "_demo", "demo": demo})

    def set_skin(self, skin: str) -> None:
        """切换前端皮肤（八套之一）。"""
        self.push({"state": "_skin", "skin": skin or "jarvis"})

    def on_message(self, message: dict[str, Any]) -> None:
        """对外暴露的消息分发入口（由 MainWindow 接线具体动作）。"""
        self.sig_message.emit(message)

    def shutdown(self) -> None:
        """关闭 WS 服务与页面。"""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
        if self._server is not None:
            self._server.close()
