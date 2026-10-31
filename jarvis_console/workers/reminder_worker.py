"""日程到点触发：每秒 tick 调度器，到点提醒（TTS + HUD 弹层由 MainWindow 接线）。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QThread, Signal, Slot

from ..core.reminders import get_scheduler
from ..logging_setup import get_logger

log = get_logger(__name__)

INTERVAL_MS = 1000


class ReminderWorker(QThread):
    """常驻提醒线程；到点发出提醒条目列表。"""

    sig_due = Signal(list)  # list[dict] 到期的提醒

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._scheduler = get_scheduler()
        self._running = False

    @Slot()
    def run(self) -> None:
        self._running = True
        while self._running:
            try:
                due = self._scheduler.tick(datetime.now())
                if due:
                    self.sig_due.emit([{
                        "id": item.id,
                        "text": item.text,
                        "when": item.when_iso,
                        "repeat": item.repeat,
                    } for item in due])
            except Exception as exc:  # noqa: BLE001 - 单次 tick 异常不应停摆
                log.debug("提醒 tick 失败：%s", exc)
            self.msleep(INTERVAL_MS)

    def stop(self) -> None:
        self._running = False
        self.quit()
