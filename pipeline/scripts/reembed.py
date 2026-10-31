"""一次性脚本：清空旧向量库，用已就绪的 bge-m3 重新嵌入 notes 表中的所有笔记。

背景：bge-m3 拉取完成前，向量库用 384 维确定性降级嵌入；bge-m3 就绪后，
只需清空向量库、从 notes 表重新嵌入，即可升级为真实 1024 维语义检索。
原文（data/raw）与笔记元数据（pipeline.db notes 表）均不动。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from core.db import init_db, get_conn
from core.embedder import Embedder
from core.vectorstore import build_store


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    settings = yaml.safe_load((root / "config" / "settings.yaml").read_text(encoding="utf-8"))

    # 1) 清空旧向量库
    vs_cfg = settings["vectorstore"]
    persist = root / vs_cfg["persist_dir"]
    vectors_db = persist / "vectors.db"
    if vectors_db.exists():
        vectors_db.unlink()
        print(f"✓ 已删除旧向量库：{vectors_db}")
    persist.mkdir(parents=True, exist_ok=True)

    # 2) 读出全部笔记
    conn = init_db()
    rows = conn.execute(
        "SELECT note_id, title, content FROM notes"
    ).fetchall()
    print(f"notes 表中共有 {len(rows)} 条笔记，开始用 bge-m3 重嵌入…")

    # 3) 批量嵌入并写库
    emb = Embedder(settings["ollama"]["base_url"], settings["ollama"]["embed_model"])
    store = build_store(vs_cfg)
    for r in rows:
        vectors = emb.embed([r["content"]])
        store.add([r["note_id"]], vectors, [r["content"]])
        print(f"  ✓ {r['title']}  ({r['note_id']})")

    print("\n重嵌入完成，知识库已升级为 bge-m3 真实语义检索。")


if __name__ == "__main__":
    main()
