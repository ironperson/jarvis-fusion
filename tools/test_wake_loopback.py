"""Verify the realtime wake pipeline without a physical microphone.

Plays synthesised speech to the default output and captures it through
"立体声混音" (Stereo Mix / loopback). If the spotter fires, the whole
capture -> resample -> decode chain works, and only the mic hardware is missing.
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAKE = os.path.join(ROOT, "models", "wakeword")
AUDIO = os.path.join(ROOT, "assets", "test_audio")
LOGS = os.path.join(ROOT, "logs")
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "wake_loopback.txt")

import numpy as np
import sherpa_onnx
import sounddevice as sd
import soundfile as sf
from imageio_ffmpeg import get_ffmpeg_exe

lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


def resample_to(x, src_sr, dst_sr):
    if src_sr == dst_sr:
        return x.astype(np.float32)
    n = len(x)
    m = int(round(n * dst_sr / src_sr))
    if m < 1 or n < 2:
        return np.zeros(0, dtype=np.float32)
    return np.interp(np.linspace(0, n - 1, m), np.arange(n), x).astype(np.float32)


def pick(preds, kind="input"):
    devs = sd.query_devices()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, d in enumerate(devs):
        if d.get(key, 0) > 0 and any(p in d["name"] for p in preds):
            return i
    for i, d in enumerate(devs):
        if d.get(key, 0) > 0:
            return i
    return None


def main():
    src = os.path.join(AUDIO, "jarvis_sentence.mp3")
    if not os.path.exists(src):
        out(f"missing {src}")
        return 1
    wav44 = os.path.join(AUDIO, "play44k.wav")
    exe = get_ffmpeg_exe()
    subprocess.run([exe, "-y", "-loglevel", "error", "-i", src, "-ar", "44100", "-ac", "2", wav44],
                   capture_output=True, timeout=60)
    data, sr = sf.read(wav44, dtype="float32")
    out(f"playback clip: {wav44}  {len(data)/sr:.2f}s @ {sr}Hz")

    out("")
    out("=== output devices ===")
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_output_channels", 0) > 0:
            out(f"  [{i}] {d['name']}  out={d['max_output_channels']}")

    def pick_out():
        """Prefer a non-WDM-KS output: WDM-KS rejects the blocking API we need."""
        devs = sd.query_devices()
        cands = [i for i, d in enumerate(devs) if d.get("max_output_channels", 0) > 0]
        for i in cands:
            if devs[i].get("hostapi") != 3:
                return i
        return cands[0] if cands else None

    loop_dev = pick(["立体声混音", "Stereo Mix", "混音"], "input")
    out_dev = pick_out()
    out(f"capture device (loopback) = {loop_dev}")
    out(f"playback device           = {out_dev}")

    os.chdir(WAKE)
    spotter = sherpa_onnx.KeywordSpotter(
        tokens="tokens.txt", encoder="encoder.onnx", decoder="decoder.onnx",
        joiner="joiner.onnx", keywords_file="keywords_custom.txt",
        num_threads=2, sample_rate=16000, provider="cpu",
        keywords_score=2.0, keywords_threshold=0.25,
    )
    out("")
    out("spotter loaded")

    mic_sr = 44100
    kws_sr = 16000
    stream = spotter.create_stream()
    detected = []
    level = {"peak": 0.0}

    def cb(indata, frames, t, status):
        mono = indata.mean(axis=1).astype(np.float32)
        level["peak"] = max(level["peak"], float(np.abs(mono).max()))
        x16 = resample_to(mono, mic_sr, kws_sr)
        if not len(x16):
            return
        stream.accept_waveform(kws_sr, x16)
        while spotter.is_ready(stream):
            spotter.decode_stream(stream)
            r = spotter.get_result(stream)
            kw = getattr(r, "keyword", r)
            if kw:
                out(f"  *** DETECTED: {kw}  at {time.strftime('%H:%M:%S')}")
                detected.append(str(kw))
        spotter.reset_stream(stream)

    out("")
    out("=== play + capture (loopback) ===")
    try:
        with sd.InputStream(device=loop_dev, channels=2, dtype="float32",
                            samplerate=mic_sr, callback=cb):
            with sd.OutputStream(device=out_dev, channels=2, dtype="float32", samplerate=sr):
                sd.play(data, samplerate=sr, device=out_dev)
                time.sleep(len(data) / sr + 2.5)
    except Exception as e:
        out(f"stream FAIL {type(e).__name__}: {str(e)[:130]}")
        return 1

    out("-" * 55)
    out(f"captured peak: {level['peak']:.5f}   (0.0 = loopback captured nothing)")
    out(f"detections: {detected}")
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
