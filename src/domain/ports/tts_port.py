"""TTSPort — text-to-speech.

New protocol shape. Donor: veda/voice/tts.py had two concrete backends
(edge-tts — network, rejected outright per REQ-M-06; pyttsx3 — kept as
the offline fallback) with no shared interface. This Protocol is what
both PiperProvider (new default) and Pyttsx3Provider (fallback) implement.

Streams raw PCM chunks rather than returning a single blob, so playback
(via tpa/audio/playback.py, itself grounded in the donor's SpeakerPlayer
in voice/audio_io.py) can start before the whole utterance is synthesised.
"""

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class TTSPort(Protocol):
    """Synthesises speech, streamed as PCM chunks."""

    @property
    def sample_rate(self) -> int:
        """Sample rate of the PCM this backend produces."""
        ...

    def synthesize(self, text: str, voice: str | None = None) -> AsyncIterator[bytes]:
        """Yield raw PCM chunks for the given text, in this backend's sample rate."""
        ...