"""Find out which microphone Veda can use, and whether it hears anything.

    python scripts/mic_check.py             # list every input device and test each one for 3 s
    python scripts/mic_check.py --device 2  # test only device 2
    python scripts/mic_check.py --seconds 5

Stop the Veda server first (only one program can hold the microphone). Speak or clap while it runs.
For each input device it reports whether Veda can open it (directly at 16 kHz mono, or at the microphone's own rate and
converted) and how loud it is:

    peak   loudest sample, 0-32767. Under ~300 with you talking = nothing useful is arriving.
    OK     opens and the level moved when you spoke.

Put the index of the best device in `mic_device_index` in config/audio.yaml.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tpa.audio.sounddevice_capture import SoundDeviceCapture  # noqa: E402

BLOCK = 480            # 30 ms at 16 kHz


def _probe(np, index: int, seconds: float) -> tuple[str, int]:
    """-> (verdict, peak). Uses Veda's own capture, so the verdict is what Veda will really get."""
    capture = SoundDeviceCapture(frame_length=BLOCK, device_index=index)
    peak = 0
    try:
        capture.start()
        deadline = time.time() + seconds
        while time.time() < deadline:
            window = capture.read(timeout=0.2)
            if window is not None:
                peak = max(peak, int(np.abs(np.frombuffer(window.pcm, dtype=np.int16).astype(np.int32)).max()))
        converted = capture._resampler is not None
    except Exception as e:  # PortAudioError, device busy, no rate accepted, ...
        return f"CANNOT OPEN: {str(e).splitlines()[0]}", 0
    finally:
        capture.stop()
    note = " (opens only at its own rate; Veda converts it to 16 kHz)" if converted else ""
    if peak < 50:
        return "opens, but silent: muted in the OS, wrong input, or unplugged" + note, peak
    if peak < 300:
        return "opens, very quiet: raise the capture volume (alsamixer, F4)" + note, peak
    return "OK" + note, peak


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", type=int, default=None, help="test only this device index")
    ap.add_argument("--seconds", type=float, default=3.0)
    args = ap.parse_args()

    try:
        import numpy as np
        import sounddevice as sd
    except Exception as e:
        print(f"Cannot load sounddevice: {e}")
        print("On a Raspberry Pi: sudo apt install -y libportaudio2   (then re-run)")
        return 1

    devices = sd.query_devices()
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = None
    inputs = [(i, d) for i, d in enumerate(devices) if d["max_input_channels"] > 0]
    if args.device is not None:
        inputs = [(i, d) for i, d in inputs if i == args.device]
    if not inputs:
        print("No input (microphone) devices found.")
        print("Check the USB mic is plugged in and shows up in:  arecord -l")
        return 1

    print(f"System default input: {default_in}\nSpeak or clap during each test ({args.seconds:.0f} s each)\n")
    best = None
    for index, d in inputs:
        mark = "  (system default)" if index == default_in else ""
        print(f"[{index}] {d['name']}{mark}   inputs={d['max_input_channels']}  native rate={int(d['default_samplerate'])} Hz")
        verdict, peak = _probe(np, index, args.seconds)
        print(f"      -> {verdict}   peak={peak}\n")
        if verdict == "OK" and (best is None or peak > best[1]):
            best = (index, peak)

    if best is None:
        print("No device gave a usable signal. See the notes above; on the Pi also run:  arecord -l  and  alsamixer")
        return 1
    print(f"Best: device {best[0]}.  Set  mic_device_index: {best[0]}  in config/audio.yaml"
          + ("  (it is already the system default, so null also works)" if best[0] == default_in else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
