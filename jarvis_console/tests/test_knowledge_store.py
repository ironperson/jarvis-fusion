"""知识库封装与会话持久化的隔离验证。"""
from __future__ import annotations

from pathlib import Path

from jarvis_console.core.notestore import mock_notestore
from jarvis_console.core.sessions import SessionStore


def test_notestore_ingest_search_delete(tmp_path: Path, tmp_db: Path) -> None:
    store = mock_notestore(tmp_path / "knowledge")
    assert store.count() == 0
    saved = store.ingest("知识管理整理", "原子笔记与双向链接的组织方法", tags=["PKM"])
    assert saved["note_id"].startswith("jarvis-")
    raw_path = Path(saved["raw_path"])
    assert raw_path.exists()
    hits = store.search("知识管理 组织方法", top_k=5)
    assert hits and hits[0]["note_id"] == saved["note_id"]
    assert store.count() == 1
    assert store.delete(saved["note_id"]) is True
    assert store.count() == 0
    assert not raw_path.exists()
    assert store.delete(saved["note_id"]) is False


def test_session_persistence(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "memory.db")
    session_id = store.new_session("会话一")
    store.append_message(session_id, "user", "你好")
    store.append_message(session_id, "assistant", "在的", [{"name": "demo"}])
    reopened = SessionStore(tmp_path / "memory.db")
    messages = reopened.load_recent(session_id, limit=10)
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[1]["tool_calls"] == [{"name": "demo"}]
    assert reopened.list_sessions()[0]["title"] == "会话一"
