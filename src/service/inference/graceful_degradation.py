"""GracefulDegradation — primary/fallback orchestrator for local inference.

Donor: veda/brain/hybrid.py's HybridClient, read in full and adapted:
  - DROPPED `_runner_holds_backend` / the "runner-busy bypass" entirely —
    that logic exists only because the donor could have a long-running
    code-agent session holding the CLI subprocess semaphore. The code
    agent is out of scope; there is no runner to be busy.
  - DROPPED `image_path`/`image_paths` params (vision out), matching
    InferencePort's signature (Batch 3).
  - The donor's per-call `governance.record_success`/`record_failure`
    circuit-breaker hooks are kept as OPTIONAL constructor callbacks
    rather than a hard import of a `veda.governance` module — this class
    should not need to know governance exists to be constructed with
    fakes (AC5). server.py wires the real governance callbacks in later.
  - The retry-once-before-falling-back behaviour is kept verbatim — it's
    real, useful behaviour, not incidental complexity.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable

from domain.ports.inference_port import InferencePort, InferenceTimeoutError
from core.logging_config import logger


class GracefulDegradation:
    """Primary/fallback wrapper that itself satisfies InferencePort."""

    def __init__(
        self,
        primary: InferencePort,
        fallback: InferencePort | None = None,
        *,
        on_success: Callable[[str], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
    ):
        self.primary = primary
        self.fallback = fallback
        self._on_success = on_success or (lambda _name: None)
        self._on_failure = on_failure or (lambda _name: None)
        self.name = f"hybrid({primary.name}+{fallback.name})" if fallback else primary.name
        self._last_was_timeout = False

    async def complete(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        timeout: int | None = None,
        **kwargs,
    ) -> str:
        result = await self._try_complete(self.primary, prompt, system, model, timeout, **kwargs)
        if result:
            return result

        if not self._last_was_timeout:
            logger.info(f"GracefulDegradation: retrying {self.primary.name} once")
            result = await self._try_complete(self.primary, prompt, system, model, timeout, **kwargs)
            if result:
                return result

        if self.fallback is None:
            return ""

        logger.info(f"GracefulDegradation: falling back to {self.fallback.name}")
        return await self._try_complete(self.fallback, prompt, system, model, timeout, **kwargs)

    async def _try_complete(
        self, client: InferencePort, prompt: str, system: str, model: str | None, timeout: int | None, **kwargs
    ) -> str:
        self._last_was_timeout = False
        try:
            result = await client.complete(prompt=prompt, system=system, model=model, timeout=timeout, **kwargs)
            if result:
                self._on_success(client.name)
                return result
            logger.warning(f"GracefulDegradation: {client.name} returned empty response")
        except InferenceTimeoutError as exc:
            self._last_was_timeout = True
            logger.warning(f"GracefulDegradation: {client.name} timed out: {exc}")
            self._on_failure(client.name)
        except Exception as exc:
            logger.warning(f"GracefulDegradation: {client.name} failed: {exc}")
            self._on_failure(client.name)
        return ""

    async def stream(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        cancel_event: asyncio.Event | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Stream from primary; fall back only if no chunk arrived yet.

        Once at least one chunk has been yielded from the primary, we are
        committed and will NOT switch mid-stream — the user is already
        hearing/seeing tokens.
        """
        yielded_any = False
        try:
            async for chunk in self.primary.stream(
                prompt=prompt, system=system, model=model, cancel_event=cancel_event, **kwargs
            ):
                yielded_any = True
                yield chunk
        except Exception as exc:
            logger.warning(f"GracefulDegradation stream: {self.primary.name} failed: {exc}")

        if yielded_any or self.fallback is None:
            return

        logger.info(f"GracefulDegradation stream: falling back to {self.fallback.name}")
        async for chunk in self.fallback.stream(
            prompt=prompt, system=system, model=model, cancel_event=cancel_event, **kwargs
        ):
            yield chunk