"""Grid probe using CALLBACK mode (WDM-KS rejects the blocking API).

WDM-KS error -9999 says 'Blocking API not supported yet', so every attempt here
supplies a callback. Also tries int16, which is the native WDM-KS format.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "mic_grid_cb.txt")

import numpy as np
import sounddevice as sd

lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


def make_cb():
    got = {"n": 0, "peak": 0.0}

    def cb(indata, frames, cb_time, status):
        got["n"] += 1
        got["peak"] = max(got["peak"], float(np.abs(indata).max()))

    return cb, got


def try_open(dev, ch, sr, bs, dtype):
    cb, got = make_cb()
    kw = dict(device=dev, channels=ch, dtype=dtype, samplerate=sr, callback=cb)
    if bs:
        kw["blocksize"] = bs
    try:
        s = sd.InputStream(**kw)
        s.start()
        time.sleep(0.6)
        s.stop()
        s.close()
        return True, got
    except Exception as e:
        return False, str(e).replace("\n", " ")[:88]


def main():
    devs = sd.query_devices()
    cands = [i for i, d in enumerate(devs) if d.get("max_input_channels", 0) > 0]
    out("input devices: " + str(cands))
    out("")
    out("=== callback-mode grid ===")
    winners = []
    for dev in cands:
        for ch in (1, 2):
            for sr in (16000, 44100, 48000):
                for dtype in ("float32", "int16"):
                    ok, info = try_open(dev, ch, sr, None, dtype)
                    if ok:
                        out(f"  OK    dev={dev} ch={ch} sr={sr} dtype={dtype}  frames={info['n']} peak={info['peak']:.5f}")
                        winners.append({"dev": dev, "ch": ch, "sr": sr, "dtype": dtype, "bs": None})
                    else:
                        if sr == 16000 and dtype == "float32" and ch == 1:
                            out(f"  FAIL  dev={dev} ch={ch} sr={sr} dtype={dtype}  {info}")
    out("")
    out("=== also try explicit blocksize on float32/16000 ===")
    for dev in cands[:1]:
        for bs in (512, 1024, 1600, 2048):
            ok, info = try_open(dev, 1, 16000, bs, "float32")
            out(f"  {'OK   ' if ok else 'FAIL '} dev={dev} ch=1 sr=16000 bs={bs}  {info if not ok else 'frames='+str(info['n'])}")
    out("-" * 55)
    out("WINNERS: " + str(winners))
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
