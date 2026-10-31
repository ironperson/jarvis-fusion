"""Token 统计与 DeepSeek 峰谷价格估算。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(slots=True)
class CostEstimate:
    """一次模型调用的 token 与人民币成本估算。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    peak: bool = False
    cost_rmb: float = 0.0

    @property
    def label(self) -> str:
        """返回状态栏显示文本。"""
        period = "峰时" if self.peak else "闲时"
        return f"tokens {self.prompt_tokens + self.completion_tokens:,}｜本轮 ≈¥{self.cost_rmb:.4f}（{period}）"


def is_peak_time(moment: datetime | None = None) -> bool:
    """判断北京时间工作日 9–13 / 14–18 是否为峰时。"""
    current = moment or datetime.now(ZoneInfo("Asia/Shanghai"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    else:
        current = current.astimezone(ZoneInfo("Asia/Shanghai"))
    if current.weekday() >= 5:
        return False
    hour = current.hour
    return 9 <= hour < 13 or 14 <= hour < 18


def estimate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_hit_tokens: int = 0,
    moment: datetime | None = None,
) -> CostEstimate:
    """按 PRD 公布的每百万 token 价格估算人民币成本。"""
    prompt = max(0, int(prompt_tokens))
    completion = max(0, int(completion_tokens))
    cached = min(prompt, max(0, int(cache_hit_tokens)))
    peak = is_peak_time(moment)
    uncached_rate = 2.0 if peak else 1.0
    cached_rate = 0.04 if peak else 0.02
    output_rate = 8.0 if peak else 4.0
    cost = ((prompt - cached) * uncached_rate + cached * cached_rate + completion * output_rate) / 1_000_000
    return CostEstimate(prompt, completion, peak, cost)
