"""知识库骨架命令行：status / search / seed / collect / tasks。"""
import argparse
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.db import init_db, count_notes, get_tasks
from core import search as search_mod
from core import pipeline as pipeline_mod


def cmd_status() -> None:
    conn = init_db()
    print(f"笔记总数：{count_notes(conn)}")


def cmd_search(query: str, top_k: int = 5) -> None:
    for r in search_mod.search(query, top_k=top_k):
        print(f"[{r['score']:.3f}] {r['title']}  ({r['note_type']}/{r['para']})")
        print(f"    {r['content'][:80]}...")
        print()


def cmd_collect(source: str, no_summary: bool) -> None:
    if source == "all":
        for r in pipeline_mod.run_all_enabled(do_summary=not no_summary):
            print("=>", r)
    else:
        print("=>", pipeline_mod.run_source(source, do_summary=not no_summary))


def cmd_tasks(limit: int, source: str | None = None) -> None:
    conn = init_db()
    for t in get_tasks(conn, source=source, limit=limit):
        print(f"[{t['status']:>6}] {t['source']:<10} {t['url']}")
        if t["error"]:
            print(f"        err: {t['error'][:80]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="贾维斯知识库骨架 CLI")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--topk", type=int, default=5)
    sub.add_parser("seed")
    cp = sub.add_parser("collect", help="采集并入库指定来源 (web/xiaohongshu/douyin/all)")
    cp.add_argument("source", nargs="?", default="web")
    cp.add_argument("--no-summary", action="store_true", help="跳过云端摘要，仅入库正文（快速验证）")
    tp = sub.add_parser("tasks", help="查看采集任务状态（对应 Aivy Mission 长任务）")
    tp.add_argument("--limit", type=int, default=50)
    tp.add_argument("--source", default=None)
    args = ap.parse_args()

    if args.cmd == "status":
        cmd_status()
    elif args.cmd == "search":
        cmd_search(args.query, args.topk)
    elif args.cmd == "seed":
        runpy.run_path(str(ROOT / "scripts" / "seed.py"), run_name="__main__")
    elif args.cmd == "collect":
        cmd_collect(args.source, args.no_summary)
    elif args.cmd == "tasks":
        cmd_tasks(args.limit, args.source)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
