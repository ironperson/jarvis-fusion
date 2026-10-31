"""日程提醒的持久化与到点判定（零 Qt 依赖，供工具与 ReminderWorker 共用）。"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger
from ..paths import REMINDERS_PATH

log = get_logger(__name__)

# 支持的时效表达：
#  - "N分钟后" / "N 分钟"  → 相对当前 N 分钟
#  - "今天HH:MM" / "HH:MM" → 今天该时刻（已过则顺延明天）
#  - "每天HH:MM"           → 每天该时刻（重复）
_RELATIVE_MIN = __import__("re").compile(r"(\d+)\s*分钟")
_CLOCK = __import__("re").compile(r"(?:今天)?\s*(\d{1,2})[:：](\d{2})")
_DAILY = __import__("re").compile(r"每天\s*(\d{1,2})[:：](\d{2})")


@dataclass(slots=True)
class Reminder:
    """单条提醒。"""

    id: str
    text: str
    when_iso: str
    repeat: str | None
    created_at: str
    done: bool = False


class ReminderScheduler:
    """把提醒落盘到 ``~/.jarvis/reminders.json`` 并判定到点。"""

    def __init__(self, path: Path | str = REMINDERS_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._items: list[Reminder] = []
        self.load()

    # —— 持久化 ——
    def load(self) -> None:
        with self._lock:
            self._items = []
            if not self.path.is_file():
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for entry in data if isinstance(data, list) else []:
                    if not isinstance(entry, dict):
                        continue
                    self._items.append(Reminder(
                        id=str(entry.get("id") or uuid.uuid4().hex[:12]),
                        text=str(entry.get("text", "")),
                        when_iso=str(entry.get("when_iso", "")),
                        repeat=entry.get("repeat"),
                        created_at=str(entry.get("created_at", "")),
                        done=bool(entry.get("done", False)),
                    ))
            except (ValueError, OSError) as exc:
                log.warning("读取提醒文件失败：%s", exc)

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = [asdict(item) for item in self._items]
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # —— 解析时效表达 ——
    @staticmethod
    def parse_when(expr: str, now: datetime | None = None) -> tuple[str, str | None]:
        """把自然语言时效转成 (ISO 时刻, 重复标记)。无法解析时 when_iso 为空。"""
        now = now or datetime.now()
        text = expr.strip()

        match = _RELATIVE_MIN.search(text)
        if match:
            minutes = int(match.group(1))
            target = now + timedelta(minutes=minutes)
            return target.isoformat(timespec="seconds"), None

        match = _DAILY.search(text)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=1)
            return target.isoformat(timespec="seconds"), "daily"

        match = _CLOCK.search(text)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=1)
            return target.isoformat(timespec="seconds"), None

        return "", None

    # —— 增删查 ——
    def add(self, text: str, when_expr: str, now: datetime | None = None) -> dict[str, Any]:
        when_iso, repeat = self.parse_when(when_expr, now)
        if not when_iso:
            return {"ok": False, "error": f"无法解析时间表达：{when_expr}"}
        reminder = Reminder(
            id=uuid.uuid4().hex[:12],
            text=text,
            when_iso=when_iso,
            repeat=repeat,
            created_at=(now or datetime.now()).isoformat(timespec="seconds"),
        )
        with self._lock:
            self._items.append(reminder)
            self.save()
        human = when_iso.replace("T", " ")
        suffix = "（每天）" if repeat else ""
        return {"ok": True, "id": reminder.id, "text": text, "when": human + suffix}

    def cancel(self, reminder_id: str) -> dict[str, Any]:
        with self._lock:
            before = len(self._items)
            self._items = [item for item in self._items if item.id != reminder_id]
            removed = before - len(self._items)
            self.save()
        return {"ok": removed > 0, "removed": removed}

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(item) for item in self._items]

    # —— 到点判定 ——
    def tick(self, now: datetime | None = None) -> list[Reminder]:
        """返回当前已到点的提醒，并处理一次性完成 / 每日重复顺延。"""
        now = now or datetime.now()
        due: list[Reminder] = []
        with self._lock:
            remaining: list[Reminder] = []
            for item in self._items:
                try:
                    when = datetime.fromisoformat(item.when_iso)
                except ValueError:
                    remaining.append(item)
                    continue
                if now >= when:
                    due.append(item)
                    if item.repeat == "daily":
                        # 顺延到明天的同一时刻，保持重复。
                        next_time = when + timedelta(days=1)
                        item.when_iso = next_time.isoformat(timespec="seconds")
                        remaining.append(item)
                    else:
                        item.done = True  # 一次性完成，不再保留。
                else:
                    remaining.append(item)
            self._items = remaining
            self.save()
        return due


_scheduler: ReminderScheduler | None = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> ReminderScheduler:
    """进程内单例；工具与 ReminderWorker 共用同一份落盘状态。"""
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = ReminderScheduler()
    return _scheduler
