"""清理测试笔记，仅保留用户真实订阅源（新华网/人民网/GitHub）。

- notes 表：删除 url 不在 KEEP_URLS 的笔记
- vectors 库（LocalStore sqlite）：同步删除对应 id
- data/raw/*.md 原文文件：同步删除
- tasks 表：清空（运行历史，下次运行自动重建）

运行：python scripts/clean_test_notes.py
"""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from core.db import init_db

# 用户真实订阅源（见 sources.yaml web.seeds）——这些保留，其余一律视为测试笔记
KEEP_URLS = {
    "https://www.xinhuanet.com/",
    "https://www.people.com.cn/",
    "https://github.com/",
}


def main() -> None:
    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    raw_dir = ROOT / settings["storage"]["raw_dir"]
    vec_dir = ROOT / settings["vectorstore"]["persist_dir"]
    vec_db = vec_dir / "vectors.db"

    conn = init_db()
    all_notes = conn.execute("SELECT note_id, title, source, url FROM notes").fetchall()
    keep = [n for n in all_notes if (n["url"] or "") in KEEP_URLS]
    delete = [n for n in all_notes if (n["url"] or "") not in KEEP_URLS]

    print(f"知识库共 {len(all_notes)} 条笔记 → 保留 {len(keep)}，删除 {len(delete)}")
    for n in delete:
        print(f"  删: {n['note_id']} | {n['source']} | {(n['title'] or '')[:32]} | {(n['url'] or '')[:42]}")

    del_ids = [n["note_id"] for n in delete]

    # 1) notes 表
    for nid in del_ids:
        conn.execute("DELETE FROM notes WHERE note_id=?", (nid,))

    # 2) tasks 表（运行历史，清空）
    tcount = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    conn.execute("DELETE FROM tasks")

    # 3) 向量库
    vec_deleted = 0
    if vec_db.exists():
        vconn = sqlite3.connect(vec_db)
        for nid in del_ids:
            cur = vconn.execute("DELETE FROM vectors WHERE id=?", (nid,))
            vec_deleted += cur.rowcount
        vconn.commit()
        vconn.close()

    # 4) 原文文件
    raw_deleted = 0
    for nid in del_ids:
        f = raw_dir / f"{nid}.md"
        if f.exists():
            f.unlink()
            raw_deleted += 1

    conn.commit()
    print(f"\n完成：notes 删 {len(del_ids)}，tasks 清空 {tcount}，vectors 删 {vec_deleted}，raw 文件删 {raw_deleted}")
    print("保留笔记：")
    for n in keep:
        print(f"  ✓ {n['note_id']} | {(n['title'] or '')[:40]} | {n['url']}")


if __name__ == "__main__":
    main()
