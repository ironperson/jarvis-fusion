"""既有 jarvis.skills.system 的只读与破坏性薄封装。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.safety import SafetyGate
from ..core.tool_registry import tool


@tool(
    name="system_status",
    description="查看当前 CPU、内存和磁盘余量。用户问电脑是否卡、资源是否紧张时使用；不要用于列进程。",
    category="system",
)
def system_status() -> str:
    """返回本机资源快照（CPU/内存/磁盘 + 网络吞吐 + 电量）。"""
    from jarvis.skills.system import system_status as existing_status

    base = existing_status()
    # 增补网络吞吐与电量（本地 API，无网络亦可显示）。
    try:
        import psutil

        counters = psutil.net_io_counters()
        up_mb = counters.bytes_sent / 1048576.0
        down_mb = counters.bytes_recv / 1048576.0
        net_part = f"网络累计 ↑{up_mb:.0f}MB ↓{down_mb:.0f}MB"
        battery = psutil.sensors_battery()
        if battery is not None:
            plug = "（接电源）" if battery.power_plugged else "（电池）"
            net_part += f"  电量 {battery.percent:.0f}%{plug}"
        else:
            net_part += "  电量 N/A（台式机）"
    except Exception:
        net_part = "网络/电量 不可用"
    return f"{base}  {net_part}"


@tool(
    name="list_processes",
    description="列出正在运行的主要程序及 PID。用户问开着什么、准备关闭某应用前核对目标时使用。",
    category="system",
)
def list_processes(limit: int = 25) -> str:
    """返回主要进程名和 PID。"""
    from jarvis.skills.system import list_apps

    return list_apps(min(100, max(1, int(limit))))


@tool(
    name="close_app",
    description=(
        "结束指定应用的全部同名进程。仅当用户明确要求关闭应用时使用；执行前一定弹窗，"
        "逐条列出命中的进程名、PID 和可读取的程序路径。不要用它处理只是卡顿诊断的请求。"
    ),
    category="system",
    destructive=True,
)
def close_app(name: str) -> dict[str, Any]:
    """复用既有批量终止函数，并把执行前命中的全部 PID 一并回报。"""
    import psutil
    from jarvis.skills.system import close_app as existing_close
    from jarvis.skills.system import resolve_app

    command, display = resolve_app(name)
    target = Path(command).name.lower()
    matches: list[dict[str, Any]] = []
    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            process_name = str(process.info.get("name") or "")
            if process_name.lower() == target:
                matches.append({
                    "name": process_name,
                    "pid": int(process.info.get("pid") or 0),
                    "pid_display": str(process.info.get("pid") or 0),
                    "path": str(process.info.get("exe") or SafetyGate._resolve_executable_path(command)),
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    message = existing_close(name)
    return {
        "ok": True,
        "display_name": display,
        "targets": matches,
        "text": f"{message}。命中目标：" + (
            "；".join(f"{item['name']} PID {item['pid']} {item['path']}" for item in matches)
            if matches else "未找到匹配进程"
        ),
    }
