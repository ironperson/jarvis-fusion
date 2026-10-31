"""Exhaustive mic probe: WDM-KS is strict about channel count and sample rate.

Also reports Windows microphone privacy consent state, which can block desktop
apps from opening any input device even when the driver enumerates fine.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "mic_diag2.txt")

import sounddevice as sd

lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


def privacy_state():
    """Read Windows microphone consent for desktop apps, if the key exists."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone",
        )
        value, _ = winreg.QueryValueEx(key, "Value")
        return str(value)
    except Exception as e:
        return f"unknown ({type(e).__name__})"


def main():
    out("=== Windows microphone privacy consent ===")
    out("  desktop-app consent Value = " + privacy_state() + "   (Allow=granted, Deny=blocked)")

    devs = sd.query_devices()
    cands = [i for i, d in enumerate(devs) if d.get("max_input_channels", 0) > 0]
    out("")
    out(f"input devices: {cands}")

    out("")
    out("=== grid probe: device x channels x samplerate ===")
    working = []
    for dev in cands:
        max_ch = devs[dev].get("max_input_channels", 1)
        for ch in sorted({1, int(max_ch)}):
            for sr in (16000, 44100, 48000):
                try:
                    s = sd.InputStream(device=dev, channels=ch, dtype="float32", samplerate=sr)
                    s.start()
                    s.stop()
                    s.close()
                    out(f"  OK    dev={dev} ch={ch} sr={sr}")
                    working.append({"device": dev, "channels": ch, "samplerate": sr})
                except Exception as e:
                    msg = str(e).replace("\n", " ")[:78]
                    out(f"  FAIL  dev={dev} ch={ch} sr={sr}  {msg}")

    out("")
    out("=== retry winners after setting sd.default.device ===")
    if working:
        w = working[0]
        try:
            sd.default.device = (w["device"], None)
            s = sd.InputStream(channels=w["channels"], dtype="float32", samplerate=w["samplerate"])
            s.start(); s.stop(); s.close()
            out(f"  OK    via default.device={w['device']}")
        except Exception as e:
            out(f"  FAIL  via default.device  {str(e)[:80]}")

    out("-" * 55)
    out(f"working configs: {working}")
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
