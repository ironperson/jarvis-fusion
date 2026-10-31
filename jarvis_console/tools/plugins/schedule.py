"""日程提醒工具（set/list/cancel），落盘 ``~/.jarvis/reminders.json``，重启仍在。"""
from __future__ import annotations

from typing import Any

from ...core.reminders import get_scheduler
from ...core.tool_registry import tool


@tool(
    name="set_reminder",
    description=(
        "设定提醒：支持“N分钟后”“今天HH:MM”“每天HH:MM”。例如“set_reminder(when='30分钟后', text='开会')”。"
        "到点后贾维斯会主动语音+HUD 提醒。"
    ),
    category="schedule",
)
def set_reminder(when: str, text: str) -> dict[str, Any]:
    """登记一条提醒。"""
    return get_scheduler().add(text, when)


@tool(
    name="list_reminders",
    description="列出当前所有未完成的提醒（含 id、内容、时间、是否每日重复）。",
    category="schedule",
)
def list_reminders() -> dict[str, Any]:
    """列出提醒。"""
    items = get_scheduler().list()
    if not items:
        return {"ok": True, "text": "当前没有待办提醒。", "items": []}
    lines = []
    for item in items:
        human = item["when_iso"].replace("T", " ")
        repeat = "（每天）" if item.get("repeat") else ""
        lines.append(f"- [{item['id']}] {human}{repeat}：{item['text']}")
    return {"ok": True, "text": "待办提醒：\n" + "\n".join(lines), "items": items}


@tool(
    name="cancel_reminder",
    description="按 id 取消一条提醒。id 可从 list_reminders 的返回获取。",
    category="schedule",
)
def cancel_reminder(reminder_id: str) -> dict[str, Any]:
    """取消提醒。"""
    return get_scheduler().cancel(reminder_id)
