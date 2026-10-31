"""Ad-hoc wake check: synthesise arbitrary text and see if the spotter fires.

Used to test whether a polyphone like 贾 (jiǎ / gǔ) is being pronounced the way
our keyword encoding expects.
"""
import asyncio
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAKE = os.path.join(ROOT, "models", "wakeword")
AUDIO = os.path.join(ROOT, "assets", "test_audio", "probe")
LOGS = os.path.join(ROOT, "logs")
os.makedirs(AUDIO, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "wake_check_text.txt")

import numpy as np
import sherpa_onnx
import soundfile as sf
import edge_tts
from imageio_ffmpeg import get_ffmpeg_exe

VOICE = "zh-CN-YunjianNeural"
lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


async def tts(text, path):
    await edge_tts.Communicate(text, VOICE).save(path)


def to_wav16k(mp3, wav):
    exe = get_ffmpeg_exe()
    r = subprocess.run([exe, "-y", "-loglevel", "error", "-i", mp3, "-ar", "16000", "-ac", "1", wav],
                       capture_output=True, timeout=60)
    return r.returncode == 0 and os.path.exists(wav)


def detect(spotter, samples, sample_rate=16000):
    stream = spotter.create_stream()
    stream.accept_waveform(sample_rate, samples.astype(np.float32))
    stream.accept_waveform(sample_rate, np.zeros(int(0.8 * sample_rate), dtype=np.float32))
    found = []
    while spotter.is_ready(stream):
        spotter.decode_stream(stream)
        r = spotter.get_result(stream)
        kw = getattr(r, "keyword", r)
        if kw:
            found.append(str(kw))
    return found


def main():
    texts = sys.argv[1:] or ["贾维斯", "甲维斯", "贾维斯，打开记事本"]
    os.chdir(WAKE)
    spotter = sherpa_onnx.KeywordSpotter(
        tokens="tokens.txt", encoder="encoder.onnx", decoder="decoder.onnx",
        joiner="joiner.onnx", keywords_file="keywords_custom.txt",
        num_threads=2, sample_rate=16000, provider="cpu",
        keywords_score=2.0, keywords_threshold=0.25,
    )
    out("spotter ready; testing texts:")
    out("-" * 58)
    for i, text in enumerate(texts):
        mp3 = os.path.join(AUDIO, f"probe{i}.mp3")
        wav = os.path.join(AUDIO, f"probe{i}.wav")
        try:
            asyncio.run(tts(text, mp3))
        except Exception as e:
            out(f"  TTS FAIL '{text}': {str(e)[:80]}")
            continue
        if not to_wav16k(mp3, wav):
            out(f"  CONVERT FAIL '{text}'")
            continue
        s, sr = sf.read(wav, dtype="float32")
        hit = detect(spotter, s, sr)
        mark = "HIT " if hit else "miss"
        out(f"  {mark}  '{text}'   dur={len(s)/sr:.2f}s  -> {hit}")
    out("-" * 58)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
