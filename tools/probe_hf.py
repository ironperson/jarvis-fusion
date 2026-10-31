"""Probe hf-mirror.com availability for the sherpa-onnx KWS model files.

Only probes hf-mirror (a China mirror of HuggingFace). Each request is given a
hard timeout so one dead host cannot hang the whole script (learning from the
earlier ghproxy hang).
"""
import os

for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)

import requests

BASE = "https://hf-mirror.com/openEuler/sherpa-kws/resolve/main"

FILES = [
    ("tokens.txt", "assets/tokens.txt"),
    ("keywords.txt", "assets/keywords.txt"),
    ("en.phone", "assets/en.phone"),
    ("encoder.onnx", "assets/onnx/encoder.onnx"),
    ("decoder.onnx", "assets/onnx/decoder.onnx"),
    ("joiner.onnx", "assets/onnx/joiner.onnx"),
]

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG = os.path.join(LOG_DIR, "probe_hf.txt")
lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


out("probing hf-mirror.com  (proxy disabled)")
out("-" * 70)
for label, path in FILES:
    url = f"{BASE}/{path}"
    try:
        r = requests.head(url, timeout=(6, 12), allow_redirects=True)
        size = r.headers.get("Content-Length", "?")
        out(f"{label:16s} HTTP {r.status_code}  size={size}")
    except Exception as e:
        out(f"{label:16s} FAIL  {type(e).__name__}: {str(e)[:70]}")
out("-" * 70)

with open(LOG, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("log:", LOG, flush=True)
