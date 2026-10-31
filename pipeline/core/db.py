"""四步法第 1 步「建仓」：SQLite 持久化（任务状态 + 笔记条目）。

tasks 表对应 Aivy「Mission 长任务」：status/retries 支持跨天续跑。
notes 表是知识库的主索引，承载原子笔记的元数据。
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from .models import Note

DEFAULT_DB = Path("D:/Desktop/MyKnowledge/pipeline.db")


def get_conn(db_path=DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path=None) -> sqlite3.Connection:
    if db_path is None:
        db_path = DEFAULT_DB
    conn = get_conn(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS notes (
            note_id TEXT PRIMARY KEY,
            title TEXT,
            content TEXT,
            source TEXT,
            url TEXT,
            note_type TEXT,
            para TEXT,
            tags TEXT,
            links TEXT,
            summary TEXT,
            raw_path TEXT,
            embedding_id TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            source TEXT,
            url TEXT,
            status TEXT,
            retries INT DEFAULT 0,
            error TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    conn.commit()
    # 迁移：M4 新增 attachments 列（关键帧/音频等本地文件路径），兼容旧库
    try:
        conn.execute("ALTER TABLE notes ADD COLUMN attachments TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 列已存在
    return conn


def upsert_note(conn: sqlite3.Connection, note: Note) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO notes
           (note_id,title,content,source,url,note_type,para,tags,links,summary,raw_path,embedding_id,created_at,attachments)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (note.note_id, note.title, note.content, note.source, note.url,
         note.note_type.value, note.para.value, ",".join(note.tags), ",".join(note.links),
         note.summary, note.raw_path, note.embedding_id, note.created_at,
         "|".join(note.attachments)),
    )
    conn.commit()


def get_note(conn: sqlite3.Connection, note_id: str):
    row = conn.execute("SELECT * FROM notes WHERE note_id=?", (note_id,)).fetchone()
    return dict(row) if row else None


def count_notes(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]


def upsert_task(conn: sqlite3.Connection, task_id: str, source: str, url: str,
                status: str, retries: int = 0, error: str = "") -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO tasks
           (task_id, source, url, status, retries, error, created_at, updated_at)
           VALUES (?,?,?,?,?,?,(SELECT created_at FROM tasks WHERE task_id=?),(SELECT updated_at FROM tasks WHERE task_id=?))""",
        (task_id, source, url, status, retries, error, task_id, task_id),
    )
    # 首次插入时 created_at 为空，补一次
    conn.execute(
        "UPDATE tasks SET created_at=?, updated_at=? WHERE task_id=? AND created_at IS NULL",
        (now, now, task_id),
    )
    conn.commit()


def get_tasks(conn: sqlite3.Connection, source: str | None = None, limit: int = 50) -> list[dict]:
    if source:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE source=? ORDER BY updated_at DESC LIMIT ?",
            (source, limit),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_stats(conn: sqlite3.Connection) -> dict:
    """看板统计：笔记总数、各源笔记数、任务状态计数、今日采集数。"""
    from datetime import datetime, timezone

    total_notes = count_notes(conn)
    notes_by_source = {}
    for row in conn.execute("SELECT source, COUNT(*) c FROM notes GROUP BY source").fetchall():
        notes_by_source[row["source"] or "unknown"] = row["c"]

    task_rows = conn.execute("SELECT status, source, created_at FROM tasks").fetchall()
    tasks_total = len(task_rows)
    tasks_done = sum(1 for r in task_rows if r["status"] == "done")
    tasks_failed = sum(1 for r in task_rows if r["status"] == "failed")
    tasks_running = sum(1 for r in task_rows if r["status"] in ("pending", "running"))

    today = datetime.now(timezone.utc).date().isoformat()
    today_collected = sum(
        1 for r in task_rows
        if (r["created_at"] or "").startswith(today) and r["status"] == "done"
    )
    collected_by_source_today = {}
    for r in task_rows:
        if (r["created_at"] or "").startswith(today) and r["status"] == "done":
            k = r["source"] or "unknown"
            collected_by_source_today[k] = collected_by_source_today.get(k, 0) + 1

    return {
        "total_notes": total_notes,
        "notes_by_source": notes_by_source,
        "tasks_total": tasks_total,
        "tasks_done": tasks_done,
        "tasks_failed": tasks_failed,
        "tasks_running": tasks_running,
        "today_collected": today_collected,
        "collected_by_source_today": collected_by_source_today,
    }
