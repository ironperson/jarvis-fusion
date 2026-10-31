"""独立 SQLite 长期记忆：向量优先、关键词兜底。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ..paths import MEMORY_DB


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_embed(text: str, dim: int = 384) -> list[float]:
    vector = [0.0] * dim
    tokens = re.findall(r"[\w]+|[\u4e00-\u9fff]", text.lower())
    for token in tokens:
        index = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % dim
        vector[index] += 1.0
    norm = math.sqrt(sum(item * item for item in vector)) or 1.0
    return [item / norm for item in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / (denominator or 1.0)


class MemoryStore:
    """在 ~/.jarvis/memory.db 中管理跨会话长期记忆。"""

    def __init__(
        self,
        db_path: Path | str = MEMORY_DB,
        ollama_base_url: str = "http://localhost:11434",
        embed_model: str = "bge-m3",
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve(strict=False)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.embed_model = embed_model
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def init_schema(self) -> None:
        """创建记忆、会话与消息表，并兼容补齐向量列。"""
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source_turn TEXT,
                    hit_count INTEGER DEFAULT 0,
                    embedding TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tool_calls TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(memories)").fetchall()}
            if "embedding" not in columns:
                connection.execute("ALTER TABLE memories ADD COLUMN embedding TEXT DEFAULT ''")
            connection.commit()

    def _embed(self, text: str) -> list[float]:
        """优先使用 Ollama bge-m3，失败时返回确定性 384 维哈希向量。"""
        try:
            response = requests.post(
                f"{self.ollama_base_url}/api/embed",
                json={"model": self.embed_model, "input": [text]},
                timeout=1.5,
            )
            response.raise_for_status()
            embeddings = response.json().get("embeddings")
            if isinstance(embeddings, list) and embeddings and isinstance(embeddings[0], list):
                return [float(item) for item in embeddings[0]]
        except (requests.RequestException, ValueError, TypeError):
            return _local_embed(text)
        return _local_embed(text)

    def remember(
        self,
        key: str,
        value: str,
        category: str = "preference",
        source: str = "",
    ) -> str:
        """写入或更新同 category/key 的记忆并返回 memory_id。"""
        clean_key = key.strip()
        clean_value = value.strip()
        if not clean_key or not clean_value:
            raise ValueError("记忆 key 和 value 不能为空")
        now = _utc_now()
        vector = json.dumps(self._embed(f"{clean_key} {clean_value}"), ensure_ascii=False)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM memories WHERE category=? AND key=?",
                (category, clean_key),
            ).fetchone()
            if existing:
                memory_id = str(existing["id"])
                connection.execute(
                    "UPDATE memories SET value=?,source_turn=?,embedding=?,updated_at=? WHERE id=?",
                    (clean_value, source, vector, now, memory_id),
                )
            else:
                memory_id = uuid.uuid4().hex[:8]
                connection.execute(
                    "INSERT INTO memories(id,category,key,value,source_turn,hit_count,embedding,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,0,?,?,?)",
                    (memory_id, category, clean_key, clean_value, source, vector, now, now),
                )
            connection.commit()
        return memory_id

    def recall(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """融合向量相似度与关键词命中召回长期记忆。"""
        clean_query = query.strip()
        if not clean_query:
            return []
        query_vector = self._embed(clean_query)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memories ORDER BY hit_count DESC, updated_at DESC LIMIT 200"
            ).fetchall()
            ranked: list[tuple[float, dict[str, Any]]] = []
            query_lower = clean_query.lower()
            query_chars = set(query_lower.replace(" ", ""))
            for row in rows:
                item = dict(row)
                haystack = f"{item['key']} {item['value']}".lower()
                lexical = 1.0 if query_lower in haystack else 0.0
                if not lexical and query_chars:
                    lexical = len(query_chars.intersection(set(haystack))) / len(query_chars)
                try:
                    memory_vector = [float(number) for number in json.loads(item.get("embedding") or "[]")]
                except (ValueError, TypeError, json.JSONDecodeError):
                    memory_vector = []
                semantic = _cosine(query_vector, memory_vector)
                score = max(semantic, lexical * 0.85) + min(int(item.get("hit_count", 0)), 10) * 0.005
                if score > 0.0:
                    item["score"] = round(score, 6)
                    item.pop("embedding", None)
                    ranked.append((score, item))
            ranked.sort(key=lambda pair: pair[0], reverse=True)
            hits = [item for _, item in ranked[: max(1, top_k)]]
            if hits:
                connection.executemany(
                    "UPDATE memories SET hit_count=hit_count+1 WHERE id=?",
                    [(item["id"],) for item in hits],
                )
                connection.commit()
            return hits

    def forget(self, memory_id: str) -> bool:
        """撤回一条长期记忆。"""
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            connection.commit()
            return cursor.rowcount > 0

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """返回最近更新的记忆，不返回向量。"""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,category,key,value,source_turn,hit_count,created_at,updated_at "
                "FROM memories ORDER BY updated_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
            return [dict(row) for row in rows]

    def auto_extract(self, user_text: str, answer: str, source_turn: str = "") -> list[str]:
        """用明确规则提取用户主动声明的偏好、路径与惯例。"""
        del answer
        candidates: list[tuple[str, str, str]] = []
        text = user_text.strip()
        remember_match = re.search(r"记住(?:一下)?[：:,，\s]*(.+)", text)
        if remember_match:
            value = remember_match.group(1).strip("。！! ")
            candidates.append((value[:24] or "用户要求", value, "preference"))
        routine_match = re.search(r"以后都(.+)", text)
        if routine_match:
            value = f"以后都{routine_match.group(1).strip()}"
            candidates.append(("长期惯例", value, "preference"))
        identity_match = re.search(r"我的(.{1,20}?)是[：:\s]*(.+)", text)
        if identity_match:
            key = f"我的{identity_match.group(1).strip()}"
            value = identity_match.group(2).strip("。！! ")
            category = "path" if any(token in value for token in ("/", "\\", ":")) else "preference"
            candidates.append((key, value, category))
        remembered: list[str] = []
        seen: set[tuple[str, str]] = set()
        for key, value, category in candidates:
            identity = (key, value)
            if value and identity not in seen:
                seen.add(identity)
                remembered.append(self.remember(key, value, category, source_turn))
        return remembered
