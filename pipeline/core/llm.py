"""模型路由（四步法第 3 步「入流」的整理环节）。

铁律（呼应「本地优先、云端仅摘要」）：
- extract()：抽取/结构化只用本地 Ollama qwen3-14b；Ollama 不可用则抛错，绝不自动升级到 DeepSeek。
- summarize()：唯一允许调用 DeepSeek 的入口；DeepSeek 不可用则降级回本地 qwen3-14b 摘要。
"""
from __future__ import annotations
import os
import requests
from pathlib import Path
import yaml


def load_settings() -> dict:
    p = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _ollama_chat(model: str, prompt: str, base_url: str) -> str:
    r = requests.post(
        f"{base_url}/api/chat",
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


def extract(text: str, settings: dict | None = None) -> str:
    """本地抽取/结构化（仅本地）。"""
    settings = settings or load_settings()
    return _ollama_chat(
        settings["ollama"]["extract_model"],
        f"请从以下内容抽取结构化要点：\n{text}",
        settings["ollama"]["base_url"],
    )


def summarize(text: str, settings: dict | None = None) -> str:
    """唯一允许走 DeepSeek 的入口；不可用时降级本地。"""
    settings = settings or load_settings()
    ds = settings["deepseek"]
    try:
        api_key = os.environ.get(ds["api_key_env"], "")
        if not api_key:
            raise RuntimeError(f"{ds['api_key_env']} 未设置")
        r = requests.post(
            f"{ds['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": ds["model"], "messages": [{"role": "user", "content": f"用中文总结要点：\n{text}"}]},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[llm] DeepSeek 不可用，降级本地摘要：{e}")
        return _ollama_chat(
            settings["ollama"]["extract_model"],
            f"用中文总结要点：\n{text}",
            settings["ollama"]["base_url"],
        )
