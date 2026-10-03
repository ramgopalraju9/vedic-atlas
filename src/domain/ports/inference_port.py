"""InferencePort — the ONLY interface allowed to produce a completion.

Donor: veda/brain/base_client.py (LLMClient Protocol), adapted:
  - Renamed LLMClient -> InferencePort, LLMTimeoutError -> InferenceTimeoutError.
    "LLM" implied a specific vendor shape; this port is backend-agnostic
    (Ollama, llama.cpp — see ADR-001).
  - Dropped `image_path` / `image_paths` params — vision is out of scope.
  - Every adapter implementing this (tpa/inference/*) must be fully local.
    Per the problem statement (REQ-M-02/M-03/M-11) no implementation of
    this Protocol may open a network socket to anywhere other than
    localhost (the Ollama server, if used, runs on-device).
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Protocol, runtime_checkable


class InferenceTimeoutError(RuntimeError):
    """Raised when a local inference backend exceeds its timeout.

    Distinct from a generic failure so graceful_degradation.py can skip
    straight to the fallback rather than retrying a backend that's
    already proven too slow for this turn.
    """


@runtime_checkable
class InferencePort(Protocol):
    """Async completion interface backed by a local model runtime."""

    @property
    def name(self) -> str:
        """Short identifier for logging (e.g. ``"ollama"``, ``"llama_cpp"``)."""
        ...

    async def complete(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        timeout: int | None = None,
        *,
        num_predict: int | None = None,
        json_schema: dict | None = None,
        temperature: float | None = None,
    ) -> str:
        """Non-streaming completion. Returns the full response text.

        `json_schema`: constrain decoding so the output is guaranteed to be
        valid JSON matching this schema (llama.cpp grammar / Ollama `format`).
        `temperature`: per-call override (low for tool-call JSON, higher for chat).
        """
        ...

    async def stream(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        """Streaming completion. Yields text deltas as they arrive."""
        ...
        yield ""  # pragma: no cover