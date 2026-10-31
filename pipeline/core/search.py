"""四步法第 4 步「检索复用（Retrieve）」：语义检索接口。"""
from __future__ import annotations
from pathlib import Path
import yaml

from core.db import init_db
from core.embedder import Embedder
from core.vectorstore import build_store


def load_settings() -> dict:
    p = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def search(query: str, top_k: int = 5, settings: dict | None = None) -> list[dict]:
    settings = settings or load_settings()
    emb = Embedder(settings["ollama"]["base_url"], settings["ollama"]["embed_model"])
    qv = emb.embed([query])[0]
    store = build_store(settings["vectorstore"])
    hits = store.query(qv, top_k=top_k)
    conn = init_db()
    out = []
    for h in hits:
        row = conn.execute("SELECT * FROM notes WHERE note_id=?", (h["id"],)).fetchone()
        if row:
            d = dict(row)
            d["score"] = h["score"]
            out.append(d)
    return out
