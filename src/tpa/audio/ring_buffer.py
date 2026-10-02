"""RingBuffer – small, bounded, in-memory-only audio buffer.

* New. Explicit implementation of the "audio stays in memory only" rule
(REQ-M-06). Used by the ambient loop / barge-in logic to hold the last
N seconds of PCM for VAD lookback without ever growing unbounded or
touching disk.
"""

from __future__ import annotations

from collections import deque


class RingBuffer:
    """Fixed-capacity FIFO of raw PCM chunks. Oldest chunk drops when full."""

    def __init__(self, max_chunks: int = 50):
        self._buffer: deque[bytes] = deque(maxlen=max_chunks)

    def push(self, chunk: bytes) -> None:
        self._buffer.append(chunk)

    def drain(self) -> bytes:
        """Return and clear all buffered audio, concatenated."""
        data = b"".join(self._buffer)
        self._buffer.clear()
        return data

    def __len__(self) -> int:
        return len(self._buffer)
