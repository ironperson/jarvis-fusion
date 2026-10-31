"""系统实时状态采样（CPU/内存/网络吞吐/电量），每秒一次，零 Qt 依赖。"""
from __future__ import annotations

import time

from .types import SysStatus


class StatusMonitor:
    """常驻采样器；``sample()`` 返回 CPU/内存/网络吞吐/电量快照。

    网络吞吐通过两次 ``net_io_counters`` 的差值计算，断开网络时自然为 0；
    电量使用 ``psutil.sensors_battery``，无电池设备（台式机）返回 -1 且 on_ac=True。
    """

    def __init__(self) -> None:
        self._last_sent: int | None = None
        self._last_recv: int | None = None
        self._last_ts: float = 0.0

    def sample(self) -> SysStatus:
        import psutil

        cpu = float(psutil.cpu_percent(interval=0.2))
        memory = psutil.virtual_memory()
        counters = psutil.net_io_counters()
        now = time.monotonic()
        net_up = 0.0
        net_down = 0.0
        if self._last_sent is not None and self._last_ts:
            delta = now - self._last_ts
            if delta > 0:
                net_up = max(0.0, (counters.bytes_sent - self._last_sent) / delta)
                net_down = max(0.0, (counters.bytes_recv - self._last_recv) / delta)
        self._last_sent = counters.bytes_sent
        self._last_recv = counters.bytes_recv
        self._last_ts = now

        battery = psutil.sensors_battery()
        battery_percent = float(battery.percent) if battery is not None else -1.0
        on_ac = bool(battery.power_plugged) if battery is not None else True
        return SysStatus(
            cpu=cpu,
            mem=float(memory.percent),
            net_up=net_up,
            net_down=net_down,
            battery=battery_percent,
            on_ac=on_ac,
        )
