from __future__ import annotations
import asyncio
from typing import AsyncIterator, Protocol, runtime_checkable

class InferenceTimeoutError(RuntimeError):
    """Raised when a local inference backend exceeds its timeout."""

@runtime_checkable
class InferencePort(Protocol):
    """Async completion interface backed by a local model runtime."""
    ...

    @property
    def name(self) -> str:
        """Short identifier for logging."""
        ...

    async def complete(
        self, 
        prompt: str, 
        system: str, 
        model: str | None = None, 
        timeout: int | None = None,
    ) -> str:
        """Non-streaming completion. Returns the full response text."""
        ...

    async def stream(self, 
        prompt: str, 
        system: str, 
        model: str | None = None, 
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        """Streaming completion. Yields text deltas as they arrive."""
        ...
        yield ""  # pragma: no cover
