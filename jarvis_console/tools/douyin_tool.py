"""抖音本地视频 ASR 能力接口位，本版不注册为工具。"""
from __future__ import annotations

from typing import Any


def douyin_asr_status() -> dict[str, Any]:
    """返回明确的后置状态，不安装 faster-whisper、不下载模型权重。"""
    return {
        "enabled": False,
        "reason": "本版未启用抖音本地视频 ASR；不支持抖音站内搜索，也不会下载 faster-whisper 权重。",
    }
