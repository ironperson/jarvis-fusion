"""每秒采样系统状态并推送（HUD 顶栏实时显示 CPU/内存/网络/电量）。"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal, Slot

from ..core.status_monitor import StatusMonitor
from ..logging_setup import get_logger

log = get_logger(__name__)

INTERVAL_MS = 1000


class StatusWorker(QThread):
    """常驻采样线程；每秒发出一份 SysStatus 字典。"""

    sig_status = Signal(dict)  # SysStatus.to_dict()

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._monitor = StatusMonitor()
        self._running = False

    @Slot()
    def run(self) -> None:
        self._running = True
        while self._running:
            try:
                status = self._monitor.sample()
                self.sig_status.emit(status.to_dict())
            except Exception as exc:  # noqa: BLE001 - 单次采样异常不应停摆
                log.debug("系统状态采样失败：%s", exc)
            self.msleep(INTERVAL_MS)

    def stop(self) -> None:
        self._running = False
        self.quit()
