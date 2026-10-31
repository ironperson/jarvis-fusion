"""Fetch a single file by URL into a target directory, with resumable logging.

Usage: python fetch_url.py <url> <dest-path>
Each invocation is a fresh process because this sandbox stalls on repeated
connections within one process.
"""
import os
import ssl
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "fetch_url.txt")
os.makedirs(os.path.dirname(LOG), exist_ok=True)

for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def log(s):
    print(s, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(s + "\n")


def main():
    if len(sys.argv) < 3:
        print("usage: fetch_url.py <url> <dest>")
        return 1
    url, dest = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        log(f"SKIP  {os.path.basename(dest)} exists ({os.path.getsize(dest)} bytes)")
        return 0
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Connection": "close",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
            total = int(r.headers.get("Content-Length") or 0)
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
        dt = time.time() - t0
        speed = len(data) / 1048576 / dt if dt > 0 else 0
        log(f"OK    {os.path.basename(dest)}  {len(data)} bytes (header said {total})  {dt:.1f}s  {speed:.1f} MB/s")
        return 0
    except Exception as e:
        log(f"FAIL  {os.path.basename(dest)}  {type(e).__name__}: {str(e)[:130]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
