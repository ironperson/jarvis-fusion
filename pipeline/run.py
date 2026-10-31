"""自唤醒调度器（对应 Aivy OS 的「自唤醒」机制）。

读取 config/sources.yaml 中各来源的 cron，到点自动触发对应采集器。
纯标准库实现（无需 APScheduler），保证本地可常驻运行；生产机装了
APScheduler 也可改为 BlockingScheduler，接口一致。

用法：
    python run.py            # 常驻，按 cron 自动唤醒采集
    python run.py --once     # 立即跑一次所有启用来源后退出（调试用）
    python run.py --web      # 同时启动 Web 看板（http://127.0.0.1:8000），调度在后台运行

注意：常驻进程会一直运行，停止请用 Ctrl+C。Windows 下可配合任务计划程序做自启。
"""
from __future__ import annotations
import argparse
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import pipeline as pipeline_mod


def _field_matches(field: str, value: int, maxv: int) -> bool:
    """cron 单字段匹配，支持 * / 数字 / 逗号列表。"""
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if part.isdigit() and int(part) == value:
            return True
    return False


def _matches(cron: str, dt: datetime) -> bool:
    parts = cron.split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    # 简化：dow 0=周日..6=周六；与 cron 约定一致
    wd = (dt.weekday() + 1) % 7
    return (
        _field_matches(minute, dt.minute, 59)
        and _field_matches(hour, dt.hour, 23)
        and _field_matches(dom, dt.day, 31)
        and _field_matches(month, dt.month, 12)
        and _field_matches(dow, wd, 6)
    )


def next_run(cron: str, after: datetime) -> datetime:
    """暴力向后搜索下一个命中的分钟（对本地低频任务足够）。"""
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(2 * 366 * 24 * 60):  # 最多搜两年
        if _matches(cron, t):
            return t
        t += timedelta(minutes=1)
    return after + timedelta(days=1)


def scheduler_loop(enabled: dict) -> None:
    """调度循环（可被主线程或后台线程调用）。"""
    print(f"[run] 自唤醒调度启动，监控来源：{', '.join(enabled)}（Ctrl+C 停止）")
    last_fired = {}
    while True:
        now = datetime.now()
        due = [name for name, cfg in enabled.items() if _matches(cfg.get("cron", ""), now)]
        due = [n for n in due if last_fired.get(n) != (now.year, now.month, now.day, now.hour, now.minute)]
        for name in due:
            last_fired[name] = (now.year, now.month, now.day, now.hour, now.minute)
            print(f"[run] 唤醒：{name} @ {now}")
            try:
                pipeline_mod.run_source(name, do_summary=True)
            except Exception as e:
                print(f"[run] {name} 执行异常：{e}")

        nxt = None
        for name, cfg in enabled.items():
            nr = next_run(cfg.get("cron", "0 0 * * *"), now)
            if nxt is None or nr < nxt:
                nxt = nr
        sleep_secs = max(30, (nxt - datetime.now()).total_seconds())
        print(f"[run] 下次唤醒：{nxt}（{int(sleep_secs)} 秒后）")
        time.sleep(min(sleep_secs, 3600))


def main() -> None:
    ap = argparse.ArgumentParser(description="贾维斯自唤醒调度器")
    ap.add_argument("--once", action="store_true", help="立即跑一次所有启用来源后退出")
    ap.add_argument("--web", action="store_true", help="同时启动 Web 看板（127.0.0.1:8000），调度在后台运行")
    args = ap.parse_args()

    sources = pipeline_mod.load_sources()
    enabled = {k: v for k, v in sources.items() if v.get("enabled")}

    if args.once:
        print("[run] --once：立即执行所有启用来源。")
        pipeline_mod.run_all_enabled(do_summary=True)
        return

    if not enabled:
        print("[run] 没有启用的来源，退出。")
        return

    if args.web:
        # 调度跑后台线程，主线程跑 Web 看板
        t = threading.Thread(target=scheduler_loop, args=(enabled,), daemon=True)
        t.start()
        import uvicorn
        print("[run] Web 看板启动：http://127.0.0.1:8000")
        uvicorn.run("api.server:app", host="127.0.0.1", port=8000, log_level="info")
    else:
        scheduler_loop(enabled)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[run] 已停止。")
