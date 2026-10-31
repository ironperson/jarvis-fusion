"""Smoke-test the wake-word detector: load the ONNX model and listen on the mic.

Usage: python test_wake.py [seconds]
Prints every detected keyword with its timestamp so we can see real detections.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAKE = os.path.join(ROOT, "models", "wakeword")
LOGS = os.path.join(ROOT, "logs")
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "wake_test.txt")

import numpy as np
import sherpa_onnx
import sounddevice as sd

lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


def resample_to(x, src_sr, dst_sr):
    """Linear-interpolation resample; the mic cannot run at 16 kHz on this box."""
    if src_sr == dst_sr:
        return x.astype(np.float32)
    n = len(x)
    m = int(round(n * dst_sr / src_sr))
    if m < 1 or n < 2:
        return np.zeros(0, dtype=np.float32)
    idx = np.linspace(0, n - 1, m)
    return np.interp(idx, np.arange(n), x).astype(np.float32)


def pick_input_device():
    """Pick a real input device; PortAudio has no usable default on this machine."""
    devs = sd.query_devices()
    preferred = ("麦克风", "Microphone", "Mic", "mic")
    for i, d in enumerate(devs):
        if d.get("max_input_channels", 0) > 0 and any(p in d["name"] for p in preferred):
            return i
    for i, d in enumerate(devs):
        if d.get("max_input_channels", 0) > 0:
            return i
    return None


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0

    out("=== audio devices ===")
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            out(f"  [{i}] {d['name']}  in={d['max_input_channels']} sr={d.get('default_samplerate')}")

    md = sys.argv[2] if len(sys.argv) > 2 else WAKE
    out(f"model dir: {md}")
    # sherpa-onnx's C++ layer segfaults when the model path contains non-ASCII
    # characters (this project lives under a Chinese-named folder). Chdir first and
    # pass bare filenames so the native code never sees the Chinese path.
    os.chdir(md)
    kw = dict(
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

    out("=== loading KeywordSpotter ===")
    t0 = time.time()
    try:
        spotter = sherpa_onnx.KeywordSpotter(**kw)
        out(f"OK  model loaded in {time.time()-t0:.2f}s")
    except Exception as e:
        out(f"FAIL  {type(e).__name__}: {e}")
        return 1

    # WDM-KS on this machine refuses 16 kHz and never fires the callback with
    # channels=1, so capture at 44.1 kHz stereo and resample down to 16 kHz.
    mic_sr = 44100
    kws_sr = 16000
    frames_per_read = int(0.1 * mic_sr)

    out(f"=== listening {seconds:.0f}s  (mic {mic_sr}Hz stereo -> {kws_sr}Hz mono) ===")

    stream = spotter.create_stream()
    detected = []
    level = {"peak": 0.0}

    def audio_cb(indata, frames, cb_time, status):
        if status:
            out(f"  audio status: {status}")
        mono = indata.mean(axis=1).astype(np.float32)
        level["peak"] = max(level["peak"], float(np.abs(mono).max()))
        x16 = resample_to(mono, mic_sr, kws_sr)
        if len(x16) == 0:
            return
        stream.accept_waveform(kws_sr, x16)
        while spotter.is_ready(stream):
            spotter.decode_stream(stream)
            r = spotter.get_result(stream)
            kw = getattr(r, "keyword", r)
            if kw:
                ts = time.strftime("%H:%M:%S")
                out(f"  *** DETECTED: {kw}  at {ts}")
                detected.append(str(kw))
        spotter.reset_stream(stream)

    dev = pick_input_device()
    out(f"using input device index: {dev}")
    try:
        with sd.InputStream(device=dev, channels=2, dtype="float32", samplerate=mic_sr,
                            blocksize=frames_per_read, callback=audio_cb):
            time.sleep(seconds)
    except Exception as e:
        out(f"mic FAIL  {type(e).__name__}: {str(e)[:140]}")
        return 1

    out("-" * 55)
    out(f"mic peak level: {level['peak']:.5f}   (0.0 would mean nothing was captured)")
    out(f"finished. detections={len(detected)}  {detected}")
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
