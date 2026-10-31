"""向量库封装（四步法第 3/4 步：入库与检索）。

默认 Chroma（按设计）；若 chromadb 未安装/不可用，自动回退到内置 numpy+SQLite 轻量库，
保证骨架在任意环境都能跑起来。两者接口一致，生产切回 chroma 只需装包即可。
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

import numpy as np


class LocalStore:
    """轻量向量库（numpy + sqlite），零额外依赖，保证骨架可运行。"""

    def __init__(self, persist_dir: str):
        self.dir = Path(persist_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = self.dir / "vectors.db"
        self._conn = sqlite3.connect(self.db)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS vectors (id TEXT PRIMARY KEY, vector BLOB, text TEXT)"
        )
        self._conn.commit()

    def add(self, ids: list[str], embeddings: list[list[float]], documents: list[str]) -> None:
        for i, e, d in zip(ids, embeddings, documents):
            blob = np.asarray(e, dtype=np.float32).tobytes()
            self._conn.execute(
                "INSERT OR REPLACE INTO vectors (id, vector, text) VALUES (?,?,?)",
                (i, blob, d),
            )
        self._conn.commit()

    def query(self, embedding: list[float], top_k: int = 5) -> list[dict]:
        emb = np.asarray(embedding, dtype=np.float32)
        rows = self._conn.execute("SELECT id, vector, text FROM vectors").fetchall()
        scored = []
        for rid, blob, text in rows:
            vec = np.frombuffer(blob, dtype=np.float32)
            denom = (np.linalg.norm(emb) * np.linalg.norm(vec)) + 1e-9
            sim = float(np.dot(emb, vec) / denom)
            scored.append((sim, rid, text))
        scored.sort(reverse=True)
        return [{"id": r[1], "score": r[0], "text": r[2]} for r in scored[:top_k]]


class ChromaStore:
    def __init__(self, persist_dir: str, collection: str = "knowledge"):
        import chromadb

        self.client = chromadb.PersistentClient(path=str(persist_dir))
        self.col = self.client.get_or_create_collection(collection)

    def add(self, ids: list[str], embeddings: list[list[float]], documents: list[str]) -> None:
        # 用 upsert 而非 add：重复运行（跑几轮）时按 id 覆盖，避免 Chroma 报 "ID 已存在"。
        self.col.upsert(ids=ids, embeddings=embeddings, documents=documents)

    def query(self, embedding: list[float], top_k: int = 5) -> list[dict]:
        res = self.col.query(query_embeddings=[embedding], n_results=top_k)
        out = []
        for rid, dist, doc in zip(res["ids"][0], res["distances"][0], res["documents"][0]):
            out.append({"id": rid, "score": 1.0 - dist, "text": doc})
        return out


def build_store(cfg: dict):
    vtype = cfg.get("type", "local")
    if vtype == "chroma":
        try:
            return ChromaStore(cfg["persist_dir"])
        except Exception as e:  # 回退，保证骨架可运行
            print(f"[vectorstore] Chroma 不可用，回退本地 store：{e}")
    return LocalStore(cfg["persist_dir"])
