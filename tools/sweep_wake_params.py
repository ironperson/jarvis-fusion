"""Sweep keywords_score / keywords_threshold to find a setting that catches the
bare wake word without falsely firing on unrelated speech.

Scoring: +1 for each true positive, -3 for each false positive on the noise clip
(a false trigger in daily use is far more annoying than a missed wake).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAKE = os.path.join(ROOT, "models", "wakeword")
AUDIO = os.path.join(ROOT, "assets", "test_audio")
WAVDIR = os.path.join(AUDIO, "wav16k")
LOGS = os.path.join(ROOT, "logs")
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "sweep.txt")

import numpy as np
import sherpa_onnx
import soundfile as sf

lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


CASES = [
    ("jarvis_only.wav", True),
    ("jarvis_sentence.wav", True),
    ("noise_other.wav", False),
]


def load_clips():
    clips = []
    for name, expect in CASES:
        p = os.path.join(WAVDIR, name)
        if os.path.exists(p):
            s, sr = sf.read(p, dtype="float32")
            clips.append((name, expect, s, sr))
        else:
            out(f"missing {p}")
    return clips


def detect(spotter, samples, sample_rate):
    stream = spotter.create_stream()
    stream.accept_waveform(sample_rate, samples.astype(np.float32))
    tail = np.zeros(int(0.8 * sample_rate), dtype=np.float32)
    stream.accept_waveform(sample_rate, tail)
    found = []
    while spotter.is_ready(stream):
        spotter.decode_stream(stream)
        r = spotter.get_result(stream)
        kw = getattr(r, "keyword", r)
        if kw:
            found.append(str(kw))
    return found


def main():
    os.chdir(WAKE)
    clips = load_clips()
    if not clips:
        out("no clips")
        return 1

    out(f"{'score':>6} {'thresh':>7} | {'only':>5} {'sent':>5} {'noise':>6} | verdict")
    out("-" * 62)
    results = []
    for score in (1.0, 2.0, 3.0, 4.0):
        for thr in (0.10, 0.15, 0.20, 0.25):
            spotter = sherpa_onnx.KeywordSpotter(
                tokens="tokens.txt", encoder="encoder.onnx", decoder="decoder.onnx",
                joiner="joiner.onnx", keywords_file="keywords_custom.txt",
                num_threads=2, sample_rate=16000, provider="cpu",
                keywords_score=score, keywords_threshold=thr,
            )
            marks = []
            tp = 0
            fp = 0
            for name, expect, s, sr in clips:
                hit = len(detect(spotter, s, sr)) > 0
                marks.append("HIT" if hit else ".")
                if hit and expect:
                    tp += 1
                elif hit and not expect:
                    fp += 1
            pts = tp - 3 * fp
            verdict = "GOOD" if (tp == 2 and fp == 0) else ("noisy" if fp else "weak")
            out(f"{score:>6.1f} {thr:>7.2f} | {marks[0]:>5} {marks[1]:>5} {marks[2]:>6} | {verdict}")
            results.append((pts, tp, fp, score, thr))

    out("-" * 62)
    results.sort(reverse=True)
    out("best settings (score by tp - 3*fp):")
    for pts, tp, fp, score, thr in results[:5]:
        out(f"   score={score}  threshold={thr}   hits={tp}/2  false={fp}  pts={pts}")
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
