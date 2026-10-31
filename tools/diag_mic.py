"""Find a mic configuration that PortAudio will actually open.

The default device is unusable on this machine (PaErrorCode -9996), and the
global device index shown by query_devices() is rejected too. Probe every
(device, hostapi, samplerate) combination and report what works.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "mic_diag.txt")

import sounddevice as sd

lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


def main():
    out("=== host APIs ===")
    try:
        for i, h in enumerate(sd.query_hostapis()):
            out(f"  [{i}] {h['name']}  devices={len(h['devices'])}  default_in={h.get('default_input_device')}")
    except Exception as e:
        out(f"  hostapi query failed: {e}")

    out("")
    out("=== all devices with input channels ===")
    devs = sd.query_devices()
    candidates = []
    for i, d in enumerate(devs):
        if d.get("max_input_channels", 0) > 0:
            out(f"  [{i}] {d['name']}  hostapi={d.get('hostapi')}  in={d['max_input_channels']}  sr={d.get('default_samplerate')}")
            candidates.append(i)

    out("")
    out("=== open-test each candidate at 16k and 44.1k ===")
    working = []
    for dev in candidates:
        for sr in (16000, 44100):
            try:
                s = sd.InputStream(device=dev, channels=1, dtype="float32", samplerate=sr)
                s.start()
                s.stop()
                s.close()
                out(f"  OK    device={dev} sr={sr}")
                working.append((dev, sr))
            except Exception as e:
                out(f"  FAIL  device={dev} sr={sr}  {type(e).__name__}: {str(e)[:80]}")

    out("")
    out("=== try (hostapi, device) tuples ===")
    try:
        hostapis = sd.query_hostapis()
        for hi, h in enumerate(hostapis):
            for di in h.get("devices", []):
                if di in candidates:
                    try:
                        s = sd.InputStream(device=(hi, di), channels=1, dtype="float32", samplerate=16000)
                        s.start(); s.stop(); s.close()
                        out(f"  OK    hostapi={hi}({h['name']}) device={di} sr=16000")
                        working.append(((hi, di), 16000))
                    except Exception as e:
                        out(f"  FAIL  hostapi={hi}({h['name']}) device={di}  {str(e)[:70]}")
    except Exception as e:
        out(f"  tuple probe failed: {e}")

    out("-" * 55)
    out(f"working configs: {working}")
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
