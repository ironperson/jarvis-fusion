"""语音播报摘要规则与峰谷成本估算的离线验证。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis_console.core.metrics import estimate_cost, is_peak_time
from jarvis_console.speech.tts import prepare_speech_text


def test_short_text_reads_full_body() -> None:
    text = "已完成，桌面已生成清单。"
    assert prepare_speech_text(text) == text


def test_long_text_uses_engine_summary() -> None:
    long_text = "正文" * 300
    spoken = prepare_speech_text(long_text, summarizer=lambda body: f"结论：{len(body)} 字")
    assert len(spoken) <= 80
    assert spoken.startswith("结论：")


def test_long_text_falls_back_to_prefix() -> None:
    def broken(body: str) -> str:
        del body
        raise RuntimeError("summary failed")

    long_text = "甲乙丙丁" * 100
    assert len(prepare_speech_text(long_text, summarizer=broken)) == 200


def test_peak_and_offpeak_costs() -> None:
    beijing = ZoneInfo("Asia/Shanghai")
    peak_moment = datetime(2026, 10, 30, 10, 0, tzinfo=beijing)
    weekend = datetime(2026, 10, 31, 10, 0, tzinfo=beijing)
    assert is_peak_time(peak_moment) is True
    assert is_peak_time(weekend) is False
    assert estimate_cost(1_000_000, 1_000_000, moment=peak_moment).cost_rmb == 10.0
    assert estimate_cost(1_000_000, 1_000_000, moment=weekend).cost_rmb == 5.0
