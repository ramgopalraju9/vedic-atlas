"""Live wake-word tester: speak into the mic and watch the "Hey Veda" score.

    python scripts/wake_test.py                 # 40 s, threshold from config
    python scripts/wake_test.py --seconds 90 --threshold 0.6

Stop the Veda server first (only one program can hold the microphone).
Say "Hey Veda" several times, then say ordinary sentences, then stay quiet. Each line shows the
peak score for that moment; "TRIGGER" marks a score at or above the threshold. Use it to find a
threshold that triggers on your voice and stays quiet on everything else; set it as
`audio.wake_threshold` in config/audio.yaml.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.config import load_full_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--device", type=int, default=None, help="input device index (default: system default)")
    args = ap.parse_args()

    import numpy as np
    import sounddevice as sd
    from openwakeword.model import Model

    cfg = load_full_config().audio
    threshold = args.threshold if args.threshold is not None else cfg.wake_threshold
    model_path = Path(cfg.wake_oww_model_path)
    model_path = model_path if model_path.is_absolute() else ROOT / model_path
    model = Model(wakeword_models=[str(model_path)], inference_framework="onnx")
    name = cfg.wake_oww_model

    print(f"model={model_path.name} threshold={threshold} - say 'Hey Veda' now, then normal sentences, then be quiet\n")
    peak, started, last_print, triggers, chunk = 0.0, time.time(), 0.0, 0, 1280
    with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=chunk, device=args.device) as stream:
        while time.time() - started < args.seconds:
            data, _ = stream.read(chunk)
            score = float(model.predict(np.asarray(data).reshape(-1)).get(name, 0.0))
            peak = max(peak, score)
            now = time.time()
            if score >= threshold:
                triggers += 1
                print(f"{now - started:5.1f}s  score {score:.2f}  {'#' * int(score * 40):<40}  TRIGGER")
            elif now - last_print > 0.5 and peak >= 0.1:
                print(f"{now - started:5.1f}s  peak {peak:.2f}  {'#' * int(peak * 40):<40}")
                last_print, peak = now, 0.0
            elif now - last_print > 0.5:
                last_print, peak = now, 0.0
    print(f"\ndone: {triggers} trigger chunk(s) at threshold {threshold}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
