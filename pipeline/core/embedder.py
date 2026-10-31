"""嵌入封装：调用本地 Ollama 的 bge-m3 生成向量。

本地优先：嵌入模型跑在用户自己的 16GB 显卡上，内容不出本机。
若 bge-m3 尚未拉取/不可用，自动降级为确定性本地向量（仅用于验证骨架机制，
非真实语义），bge-m3 就绪后无需改代码即获得真实语义检索。
"""
from __future__ import annotations
import hashlib
import math
import re

import requests


class Embedder:
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "bge-m3"):
        self.base_url = base_url
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": texts},
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["embeddings"]
        except Exception as e:
            print(
                f"[embedder] Ollama 嵌入不可用（{e}），使用本地降级嵌入（非语义，仅验证骨架）。"
                f"请拉取 {self.model} 后重跑以获得真实语义检索。"
            )
            return self._local_embed(texts)

    @staticmethod
    def _local_embed(texts: list[str], dim: int = 384) -> list[list[float]]:
        """确定性降级嵌入：哈希词袋，仅用于骨架冒烟测试。"""
        vecs = []
        for t in texts:
            v = [0.0] * dim
            for tok in re.findall(r"\w+", t.lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                v[h % dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vecs.append([x / norm for x in v])
        return vecs
