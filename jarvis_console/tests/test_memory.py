from __future__ import annotations

from pathlib import Path

from jarvis_console.core.memory import MemoryStore


def test_memory_persists_recall_and_forget(tmp_path: Path) -> None:
    database = tmp_path / "memory.db"
    first = MemoryStore(database, ollama_base_url="http://127.0.0.1:1")
    memory_id = first.remember("常用报告目录", "D:/Reports", "path", "turn-1")
    second = MemoryStore(database, ollama_base_url="http://127.0.0.1:1")
    hits = second.recall("报告目录", top_k=3)
    assert any(item["id"] == memory_id for item in hits)
    assert second.forget(memory_id) is True
    assert all(item["id"] != memory_id for item in second.recent())


def test_auto_extract_only_explicit_user_preferences(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", ollama_base_url="http://127.0.0.1:1")
    ids = store.auto_extract("记住一下：以后报告都放到 D:/Reports", "好的", "turn-2")
    assert ids
    recent = store.recent()
    assert any("D:/Reports" in item["value"] for item in recent)
