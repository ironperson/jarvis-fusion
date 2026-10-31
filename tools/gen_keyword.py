"""Encode a Chinese wake word into sherpa-onnx keyword-spotter format.

Produces models/wakeword/keywords_custom.txt, the file the KeywordSpotter reads.
Format is:  <space separated tokens> :<boost> #<threshold> @<alias>
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAKE = os.path.join(ROOT, "models", "wakeword")
LOGS = os.path.join(ROOT, "logs")
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "keyword.txt")

from sherpa_onnx import text2token

BOOST = 2.0
THRESHOLD = 0.25

lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


def encode(word: str) -> str:
    """Return the token sequence for a Chinese wake word (partial pinyin).

    The "@" marker is only used as the trailing alias in the keywords file, it is
    not part of the token sequence, so it must not be passed to text2token.
    """
    res = text2token([word], os.path.join(WAKE, "tokens.txt"), tokens_type="ppinyin")
    tokens = [t for t in res[0] if t != "@"]
    return " ".join(str(t) for t in tokens)


if __name__ == "__main__":
    words = sys.argv[1:] or ["贾维斯"]
    out("wake word encoding (tokens_type=ppinyin)")
    out("-" * 60)
    entries = []
    for w in words:
        try:
            toks = encode(w)
            out(f"{w}  ->  {toks}")
            entries.append(f"{toks} :{BOOST} #{THRESHOLD} @{w}")
        except Exception as e:
            out(f"{w}  FAIL  {type(e).__name__}: {str(e)[:110]}")

    dest = os.path.join(WAKE, "keywords_custom.txt")
    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(entries) + "\n")
    out("-" * 60)
    out(f"written: {dest}")
    for e in entries:
        out("  " + e)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
