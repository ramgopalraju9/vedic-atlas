from __future__ import annotations

import asyncio

_lock = asyncio.Semaphore(1)


class inference_lock:
    """Async context manager serialising local-inference calls.

    Usage:
        async with inference_lock():
            result = await backend.complete(...)
    """

    async def __aenter__(self) -> "inference_lock":
        await _lock.acquire()
        return self

    async def __aexit__(self, *exc_info) -> None:
        _lock.release()


def is_locked() -> bool:
    """True if an inference call is currently in flight."""
    return _lock.locked()