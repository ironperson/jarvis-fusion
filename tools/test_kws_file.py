"""Verify the wake word against synthesised speech, no microphone needed.

Feeds three clips to the KeywordSpotter:
  jarvis_only      - bare "贾维斯"               (must trigger)
  jarvis_sentence  - "贾维斯，现在几点了"          (must trigger)
  noise_other      - unrelated sentence          (must NOT trigger)
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAKE = os.path.join(ROOT, "models", "wakeword")
AUDIO = os.path.join(ROOT, "assets", "test_audio")
LOGS = os.path.join(ROOT, "logs")
WAVDIR = os.path.join(AUDIO, "wav16k")
os.makedirs(WAVDIR, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "kws_file_test.txt")

import numpy as np
import sherpa_onnx
import soundfile as sf
from imageio_ffmpeg import get_ffmpeg_exe

lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


def to_wav16k(mp3: str, wav: str) -> bool:
    exe = get_ffmpeg_exe()
    try:
        r = subprocess.run([exe, "-y", "-loglevel", "error", "-i", mp3,
                            "-ar", "16000", "-ac", "1", wav],
                           capture_output=True, timeout=60)
        return r.returncode == 0 and os.path.exists(wav)
    except Exception as e:
        out(f"  ffmpeg FAIL {type(e).__name__}: {str(e)[:90]}")
        return False


def detect(spotter, samples, sample_rate=16000):
    """Run one clip through the spotter, return list of detected keywords."""
    stream = spotter.create_stream()
    stream.accept_waveform(sample_rate, samples.astype(np.float32))
    tail = np.zeros(int(0.8 * sample_rate), dtype=np.float32)
    stream.accept_waveform(sample_rate, tail)
    found = []
    while spotter.is_ready(stream):
        spotter.decode_stream(stream)
        r = spotter.get_result(stream)
        # 1.12.x returns the keyword string directly; newer builds return an object
        kw = getattr(r, "keyword", r)
        if kw:
            found.append(str(kw))
    return found


def main():
    # chdir so the native layer never sees the Chinese path (it segfaults on it)
    os.chdir(WAKE)
    spotter = sherpa_onnx.KeywordSpotter(
        tokens="tokens.txt",
        encoder="encoder.onnx",
        decoder="decoder.onnx",
        joiner="joiner.onnx",
        keywords_file="keywords_custom.txt",
        num_threads=2,
        sample_rate=16000,
        provider="cpu",
        keywords_score=2.0,
        keywords_threshold=0.25,
    )
    out("KeywordSpotter loaded")
    out("-" * 60)

    cases = [
        ("jarvis_only.mp3", "贾维斯", True),
        ("jarvis_sentence.mp3", "贾维斯，现在几点了", True),
        ("noise_other.mp3", "今天天气不错，适合出门散步", False),
    ]

    passed = 0
    for name, text, expect_hit in cases:
        mp3 = os.path.join(AUDIO, name)
        wav = os.path.join(WAVDIR, name.replace(".mp3", ".wav"))
        if not os.path.exists(mp3):
            out(f"  MISSING {name}")
            continue
        if not to_wav16k(mp3, wav):
            out(f"  CONVERT FAIL {name}")
            continue
        samples, sr = sf.read(wav, dtype="float32")
        dur = len(samples) / sr
        found = detect(spotter, samples, sr)
        hit = len(found) > 0
        ok = (hit == expect_hit)
        passed += 1 if ok else 0
        verdict = "PASS" if ok else "FAIL"
        out(f"  [{verdict}] {name:22s} '{text}'")
        out(f"          dur={dur:.2f}s  detected={found}  expected_hit={expect_hit}")

    out("-" * 60)
    out(f"result: {passed}/{len(cases)} cases behaved as expected")
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
