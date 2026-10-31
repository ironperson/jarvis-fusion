"""Fetch one file from hf-mirror with a hard timeout, into the project models dir.

Usage: python fetch_file.py <remote-relative-path> <local-filename>
Writes a result line to logs/fetch.txt so output is never lost to console buffering.
"""
import os
import sys
import time

for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)

import hashlib
import ssl
import urllib.request

# sha256 published by the upstream sherpa-onnx KWS release. Compared after every
# download so file integrity does not depend on TLS trust (this machine sits behind
# a MITM proxy with an untrusted cert).
EXPECTED_SHA256 = {
    "tokens.txt": "2d3f32311f9b692b964da3c90e830258d3e78e013cb0c992dbfb15cd5a1a71b0",
    "encoder.onnx": "540ff509ed89bd22afe04bf7049a54bb1c95c6d8a18742ea9691910cdb5f859e",
    "decoder.onnx": "63a22dd60f40fff082ac3e09afa507f6787da36df76ded2fbe145fa233e22c21",
    "joiner.onnx": "76f7a24ed0c08633af14b2ee377f747af880d3b65eeba2cd3f31f3380fb73e8d",
}

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.join(ROOT, "models", "wakeword")
LOGS = os.path.join(ROOT, "logs")
os.makedirs(MODELS, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "fetch.txt")

BASE = "https://hf-mirror.com/openEuler/sherpa-kws/resolve/main"


def log(s):
    print(s, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(s + "\n")


def fetch(remote_path, local_name):
    url = f"{BASE}/{remote_path}"
    dest = os.path.join(MODELS, local_name)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        log(f"SKIP  {local_name} already exists ({os.path.getsize(dest)} bytes)")
        return True
    log(f"GET   {remote_path}")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Connection": "close",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
        dt = time.time() - t0
        digest = hashlib.sha256(data).hexdigest()
        want = EXPECTED_SHA256.get(local_name)
        if want:
            if digest == want:
                log(f"OK    {local_name}  {len(data)} bytes  in {dt:.1f}s  sha256 VERIFIED")
            else:
                log(f"BAD   {local_name}  sha256 MISMATCH got={digest[:16]} want={want[:16]}  DELETED")
                os.remove(dest)
                return False
        else:
            log(f"OK    {local_name}  {len(data)} bytes  in {dt:.1f}s  sha256={digest[:16]} (unverified, no published hash)")
        return True
    except Exception as e:
        log(f"FAIL  {local_name}  {type(e).__name__}: {str(e)[:110]}")
        return False


REMAINING = [
    ("assets/en.phone", "en.phone"),
    ("assets/onnx/encoder.onnx", "encoder.onnx"),
    ("assets/onnx/decoder.onnx", "decoder.onnx"),
    ("assets/onnx/joiner.onnx", "joiner.onnx"),
]


def fetch_all():
    """Fetch every remaining model file, pausing between requests.

    hf-mirror throttles rapid consecutive requests, so each request is separated
    by a short pause and runs on its own connection.
    """
    results = []
    for remote, local in REMAINING:
        results.append(fetch(remote, local))
        time.sleep(2.5)
    ok = sum(1 for r in results if r)
    log(f"---- fetched {ok}/{len(REMAINING)} files")
    return ok


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        ok = fetch(sys.argv[1], sys.argv[2])
        sys.exit(0 if ok else 1)
    ok = fetch_all()
    sys.exit(0 if ok == len(REMAINING) else 1)
