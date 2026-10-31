"""Probe which download channels actually work from this machine.

Purpose: GitHub releases were proven unreachable earlier (winget Git failed with
0x80072efd). We need a working channel for the sherpa-onnx wake-word model.
"""
import os
import sys

for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)

import requests

MODEL = "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"

CANDIDATES = [
    ("modelscope-api", f"https://www.modelscope.cn/api/v1/models/csukuangfj/{MODEL}"),
    ("modelscope-mirror-api", "https://www.modelscope.cn/api/v1/models/ZhaoChaoqun/sherpa-onnx-asr-models"),
    ("ghproxy-mirror", f"https://ghproxy.net/https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{MODEL}.tar.bz2"),
    ("direct-github", f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{MODEL}.tar.bz2"),
    ("pypi-tsinghua", "https://pypi.tuna.tsinghua.edu.cn/simple/sherpa-onnx/"),
]

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG = os.path.join(LOG_DIR, "probe.txt")
lines = []


def out(s):
    print(s)
    lines.append(s)


out("probing download channels (proxy disabled)")
out("-" * 68)
for name, url in CANDIDATES:
    try:
        r = requests.head(url, timeout=15, allow_redirects=True)
        size = r.headers.get("Content-Length", "?")
        out(f"{name:24s} HTTP {r.status_code}  size={size}")
    except Exception as e:
        out(f"{name:24s} FAIL  {type(e).__name__}: {str(e)[:70]}")
out("-" * 68)

with open(LOG, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written to " + LOG)
