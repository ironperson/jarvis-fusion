"""Verify the offline ASR (SenseVoice int8) on the clips we already synthesised.

If transcription matches the text we fed to edge-tts, the whole
audio -> text half of the pipeline is proven working, fully offline.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASR = os.path.join(ROOT, "models", "asr", "sensevoice")
WAVDIR = os.path.join(ROOT, "assets", "test_audio", "wav16k")
LOGS = os.path.join(ROOT, "logs")
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "asr_test.txt")

import numpy as np
import sherpa_onnx
import soundfile as sf

lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


CASES = [
    ("jarvis_only.wav", "贾维斯"),
    ("jarvis_sentence.wav", "贾维斯，现在几点了"),
    ("noise_other.wav", "今天天气不错，适合出门散步"),
]


def main():
    # chdir first: sherpa-onnx's native layer crashes on non-ASCII paths
    os.chdir(ASR)
    out("loading SenseVoice int8 recogniser ...")
    rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model="model.int8.onnx",
        tokens="tokens.txt",
        num_threads=4,
        use_itn=True,
        language="zh",
    )
    out("loaded")
    out("-" * 62)

    for name, expected in CASES:
        path = os.path.join(WAVDIR, name)
        if not os.path.exists(path):
            out(f"  MISSING {name}")
            continue
        samples, sr = sf.read(path, dtype="float32")
        stream = rec.create_stream()
        stream.accept_waveform(sr, samples)
        rec.decode_stream(stream)
        text = stream.result.text.strip()
        out(f"  file     : {name}")
        out(f"  expected : {expected}")
        out(f"  GOT      : {text}")
        out("")

    out("-" * 62)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
