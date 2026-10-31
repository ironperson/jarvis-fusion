"""Print the COMPLETE PortAudio error for one mic open attempt.

Earlier probes truncated the message at exactly the interesting part ('Bloc...'),
so we dump the full exception text plus device capability details.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")
os.makedirs(LOGS, exist_ok=True)
LOG = os.path.join(LOGS, "mic_err.txt")

import sounddevice as sd

lines = []


def out(s):
    print(s, flush=True)
    lines.append(s)


def main():
    dev = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    devs = sd.query_devices()
    out(f"device[{dev}] full info:")
    out("  " + repr(devs[dev]))
    out("")
    out("--- full exception text ---")
    try:
        s = sd.InputStream(device=dev, channels=1, dtype="float32", samplerate=16000)
        s.start(); s.stop(); s.close()
        out("OPENED OK")
    except Exception as e:
        out("type: " + type(e).__name__)
        out("str : " + str(e))
        out("args: " + repr(e.args))
        out("--- underlying PaErrorCode / host error ---")
        try:
            out("  " + str(e.__dict__))
        except Exception:
            pass
    out("")
    out("--- portaudio build info ---")
    try:
        out(str(sd.get_portaudio_version()))
    except Exception as e:
        out("n/a " + str(e)[:80])
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
