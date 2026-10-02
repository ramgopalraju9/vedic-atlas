"""Pyttsx3Provider – implements TTSPort via local Windows/eSpeak SAPI voices.

Donor: veda/voice/tts.py's `_synth_pyttsx3_sync`, read in full and
adapted. This is the ONLY TTS path ported from the donor – the other
half (`_synth_edge`, using `edge_tts`/`miniaudio` to hit Microsoft's Bing
Speech backend) is a hard-reject signal (network TTS, violates REQ-M-06's
spirit) and is not ported at all. Piper (tpa/tts/piper.py) is the new
default; this is the offline fallback, same role it played in the donor.
"""

from __future__ import annotations

import tempfile
import wave
from pathlib import Path
from typing import AsyncIterator


class Pyttsx3Provider:
    """Implements TTSPort via the local pyttsx3 (SAPI/eSpeak) engine."""

    def __init__(self, voice_hint: str = "zira"):
        self._voice_hint = voice_hint.lower()
        self._sample_rate = 22050  # overwritten per-call from the actual WAV header
        self._broken = False

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _synth_sync(self, text: str) -> tuple[bytes, int] | None:
        if self._broken:
            return None
        import pyttsx3

        tmp = Path(tempfile.gettempdir()) / f"veda_tts_{id(text)}.wav"
        try:
            engine = pyttsx3.init()
            for v in engine.getProperty("voices"):
                if self._voice_hint in v.name.lower() or "female" in v.name.lower():
                    engine.setProperty("voice", v.id)
                    break
            engine.setProperty("rate", 185)
            engine.save_to_file(text, str(tmp))
            engine.runAndWait()
            with wave.open(str(tmp), "rb") as wf:
                sr = wf.getframerate()
                sampwidth = wf.getsampwidth()
                raw = wf.readframes(wf.getnframes())
            if sampwidth != 2:
                return None
            return raw, sr
        except Exception:
            self._broken = True
            return None
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    async def synthesize(self, text: str, voice: str | None = None) -> AsyncIterator[bytes]:
        import asyncio

        result = await asyncio.to_thread(self._synth_sync, text)
        if result is None:
            return
        pcm, sample_rate = result
        self._sample_rate = sample_rate
        yield pcm
