"""四步法第 3 步「入流（Ingest）」：把一条 Note 原子笔记写入知识库。

流程：① 原文落盘（Markdown + YAML frontmatter）② bge-m3 嵌入 ③ 写向量库 ④ 写 SQLite 主索引。
"""
from __future__ import annotations
from pathlib import Path
import yaml

from core.db import init_db, upsert_note
from core.embedder import Embedder
from core.vectorstore import build_store
from core.models import Note


def load_settings() -> dict:
    p = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def ingest_note(note: Note, settings: dict | None = None) -> Note:
    settings = settings or load_settings()
    conn = init_db()

    # ① 原文落盘
    raw_dir = Path(settings["storage"]["raw_dir"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{note.note_id}.md"
    front = yaml.safe_dump(
        {
            "note_id": note.note_id,
            "title": note.title,
            "source": note.source,
            "url": note.url,
            "note_type": note.note_type.value,
            "para": note.para.value,
            "tags": note.tags,
            "links": note.links,
            "attachments": note.attachments,
            "created_at": note.created_at,
        },
        allow_unicode=True,
    )
    raw_path.write_text(f"---\n{front}---\n\n{note.content}\n", encoding="utf-8")
    note.raw_path = str(raw_path)

    # ② 嵌入
    emb = Embedder(settings["ollama"]["base_url"], settings["ollama"]["embed_model"])
    vectors = emb.embed([note.content])

    # ③ 写向量库
    store = build_store(settings["vectorstore"])
    store.add([note.note_id], vectors, [note.content])
    note.embedding_id = note.note_id

    # ④ 写主索引
    upsert_note(conn, note)
    return note
