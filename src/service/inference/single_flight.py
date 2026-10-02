from __future__ import annotations

import asyncio
from typing import AsyncIterator

from core.logging_config import logger
from domain.ports.inference_port import InferencePort


class SingleFlight:
    """Serialising decorator over an InferencePort."""

    def __init__(self, inner: InferencePort):
        self._inner = inner
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self._inner.name

    def __getattr__(self, item):
        # Forward adapter-specific helpers (verify_ready, warmup, ...) that
        # aren't part of InferencePort, so wrapping stays transparent.
        return getattr(self._inner, item)

    async def complete(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        timeout: int | None = None,
        **kwargs,
    ) -> str:
        if self._lock.locked():
            logger.debug("SingleFlight: inference busy, queuing")
        async with self._lock:
            return await self._inner.complete(
                prompt=prompt, system=system, model=model, timeout=timeout, **kwargs
            )

    async def stream(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        cancel_event: asyncio.Event | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        # Held for the whole stream; releasing early would let a second
        # request start decoding while this one is still producing tokens,
        # which is exactly the contention this class exists to prevent.
        async with self._lock:
            async for chunk in self._inner.stream(
                prompt=prompt, system=system, model=model, cancel_event=cancel_event, **kwargs
            ):
                yield chunk