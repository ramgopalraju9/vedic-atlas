"""FasterWhisperProvider — implements STTPort via `faster-whisper`.

Donor: veda/voice/stt.py's WhisperSTT, read in full and adapted:
  - **Behaviour change, deliberate**: the donor's docstring says "First
    call downloads the model (~50-150MB depending on size)" — that's a
    silent runtime download, which violates the no-auto-download rule
    (REQ-M-02 spirit: nothing should reach the network to make the
    device work). This adapter requires the model to already be present
    in the local cache directory and raises clearly if it isn't, instead
    of silently fetching it.
  - Takes a domain AudioWindow (PCM bytes) instead of a bare numpy array,
    converting internally — callers never need to know this adapter
    wants float32 normalized samples.
  - The donor's separate silence/RMS utterance-detection helpers
    (`rms`, `SILENCE_RMS_THRESHOLD`, etc.) are VAD logic, not STT —
    they belong with the ambient-loop / VAD work, not this file.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

from core.logging_config import logger
from domain.value_objects.audio_window import AudioWindow
from domain.value_objects.transcript import Transcript


def allow_unavailable_av() -> bool:
    """Let faster-whisper import even when PyAV's native files cannot be loaded. True when a stand-in was installed.

    faster-whisper imports PyAV (`av`) at import time, but only uses it to decode audio *files*. Veda passes a
    float32 array, so decoding never runs. On Windows with Smart App Control, one of PyAV's compiled files
    (av/sidedata/sidedata.pyd) can be blocked ("DLL load failed ... An Application Control policy has blocked
    this file"), and that import error would otherwise take the whole voice loop down on the first utterance.
    """
    try:
        import av  # noqa: F401
        return False
    except ImportError as exc:
        reason = str(exc)
    for name in [m for m in sys.modules if m == "av" or m.startswith("av.")]:
        del sys.modules[name]  # drop the half-imported package

    stub = types.ModuleType("av")

    def _unavailable(attr: str):
        raise AttributeError(
            f"PyAV is unavailable on this machine ({reason}), so audio files cannot be decoded (looked up av.{attr}). "
            "Veda only transcribes in-memory audio, which does not need it."
        )

    stub.__getattr__ = _unavailable  # type: ignore[attr-defined]  # PEP 562: only called for names the stub lacks
    sys.modules["av"] = stub
    logger.warning(f"[stt] PyAV cannot be loaded ({reason}); continuing without it (only needed for audio files)")
    return True


class FasterWhisperProvider:
    """Implements STTPort via a locally cached faster-whisper model."""

    def __init__(self, model_size: str = "base.en", cache_dir: str | Path | None = None):
        self._model_size = model_size
        self._cache_dir = str(cache_dir) if cache_dir else None
        self._model = None

    def _ensure(self):
        if self._model is None:
            allow_unavailable_av()
            from faster_whisper import WhisperModel

            kwargs = {"device": "cpu", "compute_type": "int8"}
            if self._cache_dir:
                kwargs["download_root"] = self._cache_dir
                if not Path(self._cache_dir).exists():
                    raise FileNotFoundError(
                        f"whisper model cache not found at {self._cache_dir} — this build does "
                        "not auto-download models. Run the installer's model-fetch step first."
                    )
            self._model = WhisperModel(self._model_size, **kwargs)
        return self._model

    async def transcribe(self, audio: AudioWindow) -> Transcript:
        import numpy as np

        def _do():
            pcm = np.frombuffer(audio.pcm, dtype=np.int16).astype(np.float32) / 32768.0
            segments, info = self._ensure().transcribe(
                pcm, language="en", vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                # Whisper invents text on noise ("subscribe to our channel", "Bye. Bye. Bye..."), and
                # conditioning on its own previous output makes it loop: turn that off and reject
                # low-confidence / repetitive output.
                condition_on_previous_text=False,
                no_speech_threshold=0.6, log_prob_threshold=-1.0, compression_ratio_threshold=2.4,
            )
            # Whisper's own recommended rule: a segment that is probably silence AND low confidence is noise.
            kept = [
                seg for seg in segments
                if not (getattr(seg, "no_speech_prob", 0.0) > 0.6 and getattr(seg, "avg_logprob", 0.0) < -1.0)
            ]
            text = "".join(seg.text for seg in kept).strip()
            return text, getattr(info, "language_probability", 1.0)

        import asyncio

        text, confidence = await asyncio.to_thread(_do)
        return Transcript(text=text, confidence=float(confidence), duration_sec=audio.duration_sec)