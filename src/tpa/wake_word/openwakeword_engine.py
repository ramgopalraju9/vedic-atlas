"""OpenWakeWordEngine — implements WakeWordPort via `openwakeword`.

★ New. Open-source, no-API-key replacement for PorcupineWakeWord — see
docs/voice/open-source-wake-speaker-design.md §4.1 for the full design.
openwakeword is frame-driven like Porcupine (not windowed like
Resemblyzer), so this adapter buffers raw frames up to the engine's
preferred chunk size (1280 samples = 80ms @ 16kHz, openwakeword's own
recommendation) and calls `Model.predict()` once per full chunk,
carrying any remainder over to the next `process()` call — same slicing
idiom PorcupineWakeWord's caller already uses in
service/voice/voice_session.py, just performed inside the adapter here
since openwakeword's native chunk size doesn't match frame_length's
reported value the way Porcupine's does.

The configured wake phrase ("Hey Veda") is a custom-trained model, not
one of openwakeword's bundled pretrained words — model_path is required
in practice (see config_schemas.py's AudioConfig.wake_oww_model_path).
"""

from __future__ import annotations

from pathlib import Path


class OpenWakeWordEngine:
    """Implements WakeWordPort via openwakeword."""

    def __init__(
        self,
        model_name: str = "hey_veda",
        model_path: str | None = None,
        threshold: float = 0.5,
        chunk_samples: int = 1280,
        sample_rate: int = 16000,
    ):
        if model_path:
            path = Path(model_path)
            if not path.exists():
                raise FileNotFoundError(f"openwakeword model not found at {path}")
        from openwakeword.model import Model

        # None -> openwakeword's own bundled pretrained set; only used if a
        # pretrained word is ever selected instead of the custom "Hey Veda" model.
        paths = [model_path] if model_path else None
        self._model = Model(wakeword_models=paths, inference_framework="onnx")
        self._model_name = model_name
        self._threshold = threshold
        self._chunk_samples = chunk_samples
        self._sample_rate = sample_rate
        self._buf = bytearray()

    @property
    def frame_length(self) -> int:
        return self._chunk_samples

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def process(self, frame: bytes) -> bool:
        import numpy as np

        self._buf += frame
        chunk_bytes = self._chunk_samples * 2  # int16 PCM = 2 bytes/sample
        triggered = False
        while len(self._buf) >= chunk_bytes:
            chunk, self._buf = bytes(self._buf[:chunk_bytes]), self._buf[chunk_bytes:]
            pcm = np.frombuffer(chunk, dtype=np.int16)
            scores = self._model.predict(pcm)
            if scores.get(self._model_name, 0.0) >= self._threshold:
                triggered = True
        return triggered

    def start(self) -> None:
        # Frame-driven; no background thread. Present so the voice loop can
        # drive every wake engine through the same start/stop pair.
        pass

    def stop(self) -> None:
        # Model has no explicit teardown.
        pass
