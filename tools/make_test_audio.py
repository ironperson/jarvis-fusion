"""Synthesise test speech with edge-tts so the wake word can be verified offline.

This lets us prove the "贾维斯" encoding actually triggers the model without
requiring the user to speak into the microphone.
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO = os.path.join(ROOT, "assets", "test_audio")
LOGS = os.path.join(ROOT, "logs")
os.makedirs(AUDIO, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "tts_gen.txt")

import edge_tts

VOICE = "zh-CN-YunjianNeural"

SAMPLES = [
    ("jarvis_only.mp3", "贾维斯"),
    ("jarvis_sentence.mp3", "贾维斯，现在几点了"),
    ("noise_other.mp3", "今天天气不错，适合出门散步"),
]

lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


async def gen(text: str, path: str):
    comm = edge_tts.Communicate(text, VOICE)
    await comm.save(path)


def main():
    out(f"edge-tts voice: {VOICE}")
    out("-" * 55)
    for name, text in SAMPLES:
        dest = os.path.join(AUDIO, name)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            out(f"SKIP  {name} exists ({os.path.getsize(dest)} bytes)")
            continue
        try:
            asyncio.run(gen(text, dest))
            size = os.path.getsize(dest) if os.path.exists(dest) else 0
            out(f"OK    {name}  '{text}'  {size} bytes")
        except Exception as e:
            out(f"FAIL  {name}  {type(e).__name__}: {str(e)[:110]}")
    out("-" * 55)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
