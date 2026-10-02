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

from pathlib import Path

from domain.value_objects.audio_window import AudioWindow
from domain.value_objects.transcript import Transcript


class FasterWhisperProvider:
    """Implements STTPort via a locally cached faster-whisper model."""

    def __init__(self, model_size: str = "base.en", cache_dir: str | Path | None = None):
        self._model_size = model_size
        self._cache_dir = str(cache_dir) if cache_dir else None
        self._model = None

    def _ensure(self):
        if self._model is None:
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
            segments, info = self._ensure().transcribe(pcm, language="en", vad_filter=True)
            text = "".join(seg.text for seg in segments).strip()
            return text, getattr(info, "language_probability", 1.0)

        import asyncio

        text, confidence = await asyncio.to_thread(_do)
        return Transcript(text=text, confidence=float(confidence), duration_sec=audio.duration_sec)