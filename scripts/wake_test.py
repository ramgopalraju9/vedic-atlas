"""Live wake-word tester: speak into the mic and watch the microphone level and the "Hey Veda" score.

    python scripts/wake_test.py                          # 40 s, threshold from config, system default mic
    python scripts/wake_test.py --device 1               # a specific input device (see scripts/mic_check.py)
    python scripts/wake_test.py --seconds 90 --threshold 0.3

Stop the Veda server first (only one program can hold the microphone).
Say "Hey Veda" several times, then say ordinary sentences, then stay quiet. A line is printed every half second:

    level   how loud the microphone is (peak of the last half second, 0-32767). Near 0 while you talk = the mic
            is not delivering your voice (wrong device, muted, capture volume too low).
    score   the wake model's highest score in that half second (0-1). "TRIGGER" = at or above the threshold.

At the end it summarises the loudest level and the highest score and says what that means. Use the scores to pick
`audio.wake_threshold` in config/audio.yaml: it should trigger on your "Hey Veda" and stay quiet on everything else.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.config import load_full_config  # noqa: E402
from tpa.audio.sounddevice_capture import SoundDeviceCapture  # noqa: E402

CHUNK = 1280            # 80 ms at 16 kHz, what openwakeword expects
QUIET_LEVEL = 300       # below this the microphone is effectively silent
PRINT_EVERY = 0.5


def _bar(value: float, full: float, width: int = 24) -> str:
    return "#" * int(max(0.0, min(1.0, value / full)) * width)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--device", type=int, default=None, help="input device index (default: system default)")
    args = ap.parse_args()

    import numpy as np
    from tpa.wake_word.openwakeword_engine import require_feature_models

    require_feature_models()   # a clear message if openwakeword's helper models were never downloaded
    from openwakeword.model import Model

    cfg = load_full_config().audio
    threshold = args.threshold if args.threshold is not None else cfg.wake_threshold
    model_path = Path(cfg.wake_oww_model_path)
    model_path = model_path if model_path.is_absolute() else ROOT / model_path
    model = Model(wakeword_models=[str(model_path)], inference_framework="onnx")
    name = cfg.wake_oww_model

    # The same capture Veda uses, so a microphone that only accepts 44.1/48 kHz is converted to 16 kHz here too.
    capture = SoundDeviceCapture(frame_length=CHUNK, device_index=args.device)
    capture.start()
    converted = " (converted to 16 kHz)" if capture._resampler is not None else ""
    print(f"model={model_path.name}  threshold={threshold}  device={args.device if args.device is not None else 'system default'}{converted}")
    print("Say 'Hey Veda' now, then normal sentences, then be quiet.\n")

    started = time.time()
    last_print = started
    window_level, window_score = 0, 0.0
    best_level, best_score, triggers, chunks, names_checked = 0, 0.0, 0, 0, False
    try:
        while time.time() - started < args.seconds:
            window = capture.read(timeout=1.0)
            if window is None:
                continue
            samples = np.frombuffer(window.pcm, dtype=np.int16)
            scores = model.predict(samples)
            if not names_checked:
                names_checked = True
                if name not in scores:
                    print(f"WARNING: the model reports {sorted(scores)} but config expects '{name}' "
                          f"(audio.wake_oww_model); scores below will read 0.\n")
            chunks += 1
            level = int(np.abs(samples.astype(np.int32)).max())
            score = float(scores.get(name, 0.0))
            window_level, window_score = max(window_level, level), max(window_score, score)
            best_level, best_score = max(best_level, level), max(best_score, score)
            now = time.time()
            if now - last_print >= PRINT_EVERY:
                flag = "  TRIGGER" if window_score >= threshold else ""
                triggers += window_score >= threshold
                print(f"{now - started:5.1f}s  level {window_level:5d} {_bar(window_level, 12000):<24}  "
                      f"score {window_score:.2f} {_bar(window_score, 1.0):<24}{flag}")
                last_print, window_level, window_score = now, 0, 0.0
    finally:
        capture.stop()

    print(f"\ndone: {chunks} chunks heard, loudest level {best_level}, highest score {best_score:.2f}, "
          f"{triggers} trigger(s) at threshold {threshold}")
    if chunks == 0:
        print("-> NO AUDIO ARRIVED. Wrong device? Run scripts/mic_check.py and pass --device <index>.")
    elif best_level < QUIET_LEVEL:
        print("-> The microphone is silent or very quiet. Fix this first: check the device (scripts/mic_check.py), "
              "and the capture volume / mute in alsamixer (F4).")
    elif best_score < 0.1:
        print("-> The microphone is fine, but the wake model never scored your voice above 0.1. Lowering the "
              "threshold will not help: the model needs retraining with real recordings of your voice.")
    elif best_score < threshold:
        print(f"-> The model reacts to you but never reached {threshold}. Try --threshold {max(0.1, best_score * 0.8):.2f} "
              "and check that ordinary speech does not trigger it.")
    else:
        print("-> Working: the wake word triggered. Check that ordinary speech does not (run it again without saying 'Hey Veda').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
