"""MyKnowledge 的唯一读写、备份与撤回入口。"""
from __future__ import annotations

import copy
import hashlib
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from typing import Any, Iterator

from ..paths import KNOWLEDGE_DB

_PIPELINE_LOCK = threading.RLock()


class NoteStore:
    """薄封装既有 pipeline，并防止测试/Mock 写入真实知识库。"""

    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        db_path: Path | str | None = None,
    ) -> None:
        from core import db as core_db
        from ingest.ingest import load_settings

        self.db_path = Path(db_path or core_db.DEFAULT_DB).expanduser().resolve(strict=False)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        loaded = load_settings() if settings is None else settings
        self.settings = copy.deepcopy(loaded)
        if self.db_path != KNOWLEDGE_DB.resolve(strict=False):
            safe_root = self.db_path.parent
            self.settings.setdefault("storage", {})["raw_dir"] = str(safe_root / "raw")
            vector = self.settings.setdefault("vectorstore", {})
            vector["type"] = "local"
            vector["persist_dir"] = str(safe_root / "vectors")
        self._backup_done_date = ""

    @contextmanager
    def _db_scope(self) -> Iterator[None]:
        """让 pipeline 无参 init_db 在当前封装调用期间使用指定 DB。"""
        from core import db as core_db

        with _PIPELINE_LOCK:
            original = core_db.DEFAULT_DB
            core_db.DEFAULT_DB = self.db_path
            try:
                yield
            finally:
                core_db.DEFAULT_DB = original

    def _note_id(self, url: str = "") -> str:
        """按 URL 稳定生成 ID；无 URL 时生成短 UUID。"""
        if url.strip():
            digest = hashlib.md5(url.strip().encode("utf-8")).hexdigest()[:10]
        else:
            digest = uuid.uuid4().hex[:10]
        return f"jarvis-{digest}"

    def _backup_if_needed(self) -> None:
        """每日首写前备份 pipeline.db，且仅保留最近七份。"""
        if not self.db_path.exists():
            return
        date_text = datetime.now().strftime("%Y%m%d")
        if self._backup_done_date == date_text:
            return
        backup_dir = self.db_path.parent / "_backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"pipeline-{stamp}.db"
        if self.db_path.stat().st_size > 0:
            source = sqlite3.connect(self.db_path)
            destination = sqlite3.connect(backup_path)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
        backups = sorted(backup_dir.glob("pipeline-*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in backups[7:]:
            stale.unlink(missing_ok=True)
        self._backup_done_date = date_text

    def count(self) -> int:
        """返回知识库笔记数。"""
        from core.db import count_notes, init_db

        with self._db_scope():
            connection = init_db()
            try:
                return int(count_notes(connection))
            finally:
                connection.close()

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """调用既有向量检索并返回 notes 全字段。"""
        from core.search import search

        with self._db_scope():
            return search(query, top_k=max(1, top_k), settings=self.settings)

    def ingest(
        self,
        title: str,
        content: str,
        source: str = "jarvis_console",
        tags: list[str] | None = None,
        url: str = "",
    ) -> dict[str, Any]:
        """备份后写入原子笔记，返回 note_id 与完整原文路径。"""
        from core.models import Note, NoteType, ParaCategory
        from ingest.ingest import ingest_note

        if not title.strip() or not content.strip():
            raise ValueError("笔记标题和正文不能为空")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_if_needed()
        note = Note(
            note_id=self._note_id(url),
            title=title.strip(),
            content=content.strip(),
            source=source.strip() or "jarvis_console",
            url=url.strip(),
            note_type=NoteType.LITERATURE,
            para=ParaCategory.RESOURCE,
            tags=list(tags or []),
        )
        with self._db_scope():
            saved = ingest_note(note, self.settings)
        return {
            "ok": True,
            "note_id": saved.note_id,
            "title": saved.title,
            "raw_path": str(Path(saved.raw_path).resolve(strict=False)),
            "text": f"已沉淀进知识库：{saved.title}（note_id: {saved.note_id}，原文：{Path(saved.raw_path).resolve(strict=False)}）",
        }

    def delete(self, note_id: str) -> bool:
        """撤回笔记：删除主索引、向量记录和原文 Markdown。"""
        from core.db import init_db

        self._backup_if_needed()
        with self._db_scope():
            connection = init_db()
            row = connection.execute(
                "SELECT raw_path,embedding_id FROM notes WHERE note_id=?", (note_id,)
            ).fetchone()
            if row is None:
                connection.close()
                return False
            raw_path = Path(str(row["raw_path"] or "")) if row["raw_path"] else None
            embedding_id = str(row["embedding_id"] or note_id)
            connection.execute("DELETE FROM notes WHERE note_id=?", (note_id,))
            connection.commit()
            connection.close()
        if raw_path is not None:
            raw_path.unlink(missing_ok=True)
        vector_cfg = self.settings.get("vectorstore", {})
        local_db = Path(str(vector_cfg.get("persist_dir", ""))) / "vectors.db"
        if local_db.exists():
            with sqlite3.connect(local_db) as vector_connection:
                vector_connection.execute("DELETE FROM vectors WHERE id=?", (embedding_id,))
                vector_connection.commit()
        if str(vector_cfg.get("type", "")) == "chroma":
            try:
                import chromadb

                client = chromadb.PersistentClient(path=str(vector_cfg.get("persist_dir")))
                collection = client.get_or_create_collection("knowledge")
                collection.delete(ids=[embedding_id])
            except (ImportError, OSError, ValueError, RuntimeError):
                pass
        return True


def mock_notestore(root: Path | str) -> NoteStore:
    """创建完全隔离的 Mock/测试知识库。"""
    base = Path(root).expanduser().resolve(strict=False)
    settings = {
        "ollama": {"base_url": "http://127.0.0.1:1", "embed_model": "bge-m3"},
        "vectorstore": {"type": "local", "persist_dir": str(base / "vectors")},
        "storage": {"raw_dir": str(base / "raw")},
    }
    return NoteStore(settings=settings, db_path=base / "pipeline.db")
