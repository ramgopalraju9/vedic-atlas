"""PiperProvider – implements TTSPort via the local Piper neural TTS engine.

* New – no donor equivalent. Piper wasn't in the donor at all (it used
edge-tts + pyttsx3); this is the new default per the roadmap's privacy
plan (docs/roadmap/09-privacy-compliance.md): a fully local neural voice,
replacing edge-tts's network dependency without pyttsx3's robotic
quality. Requires a pre-downloaded `.onnx` voice model – no auto-download,
consistent with every other model-loading adapter in this codebase.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator


class PiperProvider:
    """Implements TTSPort via a locally installed Piper voice model."""

    def __init__(self, model_path: str | Path, sample_rate: int = 22050):
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Piper voice model not found at {path} – this build does not auto-download "
                "voice models. Run the installer's model-fetch step first."
            )
        self._model_path = path
        self._sample_rate = sample_rate
        self._voice = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _ensure(self):
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(str(self._model_path))
        return self._voice

    async def synthesize(self, text: str, voice: str | None = None) -> AsyncIterator[bytes]:
        import asyncio

        voice_model = await asyncio.to_thread(self._ensure)

        def _synth_chunks() -> list[bytes]:
            return [chunk.audio_int16_bytes for chunk in voice_model.synthesize(text)]

        for chunk in await asyncio.to_thread(_synth_chunks):
            yield chunk
