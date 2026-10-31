"""贾维斯知识库 Web 看板（设计文档 §6）。

后端：FastAPI，端口 8000。
- GET  /            → 单页看板前端（api/static/index.html）
- GET  /api/stats    → 总条目数、各源笔记数、任务状态、今日采集数
- GET  /api/tasks    → 采集任务列表（对应 Aivy「Mission 长任务」），支持 ?status= &source= &limit=
- GET  /api/items    → 语义检索结果，?q= 查询 &topk=
- POST /api/trigger  → 手动触发某来源采集（?source=web），后台线程执行，可轮询 /api/stats 查看进展

运行：python -m uvicorn api.server:app --host 127.0.0.1 --port 8000
（或用项目根 run.py 统一启动，run.py 已预留挂载本服务）
"""
from __future__ import annotations
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from core import db as db_mod
from core import search as search_mod
from core import pipeline as pipeline_mod

STATIC_DIR = ROOT / "api" / "static"
INDEX_HTML = STATIC_DIR / "index.html"

app = FastAPI(title="贾维斯知识库看板", version="0.1")

# 后台触发的运行态（最简内存态，够看板轮询）
_run_lock = threading.Lock()
_last_run = {"source": None, "status": "idle", "result": None, "started_at": None}


def _do_run(source: str) -> None:
    global _last_run
    with _run_lock:
        _last_run = {**_last_run, "status": "running"}
    try:
        result = pipeline_mod.run_source(source, do_summary=True)
        with _run_lock:
            _last_run = {
                "source": source, "status": "done", "result": result, "started_at": _last_run.get("started_at"),
            }
    except Exception as e:  # 捕获异常，避免线程静默失败
        with _run_lock:
            _last_run = {
                "source": source, "status": "error", "result": {"error": str(e)},
                "started_at": _last_run.get("started_at"),
            }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(INDEX_HTML))


@app.get("/api/stats")
def stats() -> dict:
    conn = db_mod.init_db()
    s = db_mod.get_stats(conn)
    with _run_lock:
        s["last_run"] = dict(_last_run)
    return s


@app.get("/api/sources")
def sources() -> list[dict]:
    src = pipeline_mod.load_sources()
    out = []
    for name, cfg in src.items():
        out.append({"name": name, "enabled": bool(cfg.get("enabled", False))})
    return out


@app.get("/api/tasks")
def tasks(status: str | None = None, source: str | None = None, limit: int = 100) -> list[dict]:
    conn = db_mod.init_db()
    rows = db_mod.get_tasks(conn, source=source, limit=limit)
    if status:
        rows = [r for r in rows if r["status"] == status]
    return rows


@app.get("/api/items")
def items(q: str = "", topk: int = 5) -> list[dict]:
    if not q.strip():
        return []
    hits = search_mod.search(q, top_k=topk)
    out = []
    for h in hits:
        out.append({
            "note_id": h.get("note_id"),
            "title": h.get("title"),
            "source": h.get("source"),
            "score": round(float(h.get("score", 0.0)), 3),
            "url": h.get("url"),
            "snippet": (h.get("content") or "")[:160],
        })
    return out


@app.post("/api/trigger")
async def trigger(request: Request, source: str = "web") -> JSONResponse:
    sources = pipeline_mod.load_sources()
    if source not in sources:
        return JSONResponse({"ok": False, "error": f"未知来源：{source}"}, status_code=400)
    if not sources[source].get("enabled", False):
        return JSONResponse({"ok": False, "error": f"来源 {source} 未启用（sources.yaml）"}, status_code=400)
    with _run_lock:
        running = _last_run.get("status") == "running"
        if running:
            return JSONResponse({"ok": False, "error": "已有采集任务在后台运行，请稍后再触发"}, status_code=409)
        from datetime import datetime, timezone
        _last_run["started_at"] = datetime.now(timezone.utc).isoformat()
    t = threading.Thread(target=_do_run, args=(source,), daemon=True)
    t.start()
    return JSONResponse({"ok": True, "source": source, "message": "已在后台触发，可轮询 /api/stats 查看进展"})
