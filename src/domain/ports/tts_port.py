from typing import AsyncIterator, Protocol, runtime_checkable

@runtime_checkable
class TTSPort(Protocol):
    """Synthesises speech, streamed as PCM chunks."""

    @property
    def sample_rate(self) -> int:
        """Sample rate of the PCM this backend produces."""
        ...

    def synthesise(self, text: str, voice: str | None = None) -> AsyncIterator[bytes]:
        """Yield raw PCM chunks for the given text, in this backend's sample rate."""
        ...
