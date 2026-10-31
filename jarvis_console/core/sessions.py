"""会话与消息持久化。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..paths import MEMORY_DB
from .memory import MemoryStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """在 memory.db 的 sessions/messages 表中保存对话历史。"""

    def __init__(self, db_path: Path | str = MEMORY_DB) -> None:
        self.db_path = Path(db_path).expanduser().resolve(strict=False)
        MemoryStore(self.db_path).init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def new_session(self, title: str = "新会话") -> str:
        """创建会话并返回 session_id。"""
        session_id = uuid.uuid4().hex[:12]
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions(session_id,title,started_at,updated_at) VALUES(?,?,?,?)",
                (session_id, title.strip() or "新会话", now, now),
            )
            connection.commit()
        return session_id

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        """追加消息并刷新会话时间。"""
        now = _now()
        encoded_calls = json.dumps(tool_calls or [], ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO messages(session_id,role,content,tool_calls,created_at) VALUES(?,?,?,?,?)",
                (session_id, role, content, encoded_calls, now),
            )
            connection.execute(
                "UPDATE sessions SET updated_at=? WHERE session_id=?",
                (now, session_id),
            )
            connection.commit()

    def load_recent(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """按时间正序返回最近消息。"""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT role,content,tool_calls,created_at FROM messages WHERE session_id=? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, max(1, limit)),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in reversed(rows):
            item = dict(row)
            try:
                item["tool_calls"] = json.loads(item.get("tool_calls") or "[]")
            except json.JSONDecodeError:
                item["tool_calls"] = []
            output.append(item)
        return output

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """返回最近更新的会话。"""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id,title,started_at,updated_at FROM sessions "
                "ORDER BY updated_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
            return [dict(row) for row in rows]
