"""LookupHealthService — probes each online tool dependency with a known query.

Each probe is a small async callable composed in server.py (the composition
root), so this service knows nothing about weather, Tavily, or HTTP. A probe
returns a short human-readable sample on success and raises on failure.
A probe whose secret is missing is reported as "not configured" without
making a network call.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from core.logging_config import logger
from domain.entities.provider_health import ProviderHealth

_PROBE_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class HealthProbe:
    name: str
    check: Callable[[], Awaitable[str]]
    configured: Callable[[], bool] = lambda: True
    missing_hint: str = ""


class LookupHealthService:
    def __init__(self, probes: list[HealthProbe], timeout_sec: float = _PROBE_TIMEOUT_SEC):
        self._probes = probes
        self._timeout = timeout_sec

    async def run(self) -> list[ProviderHealth]:
        return list(await asyncio.gather(*(self._run_one(p) for p in self._probes)))

    async def _run_one(self, probe: HealthProbe) -> ProviderHealth:
        if not probe.configured():
            result = ProviderHealth(probe.name, False, False, 0, probe.missing_hint or "not configured")
            logger.warning(f"[health] {probe.name}: not configured ({result.detail})")
            return result
        started = time.perf_counter()
        try:
            detail = await asyncio.wait_for(probe.check(), timeout=self._timeout)
            ok = True
        except asyncio.TimeoutError:
            ok, detail = False, f"timed out after {self._timeout:.0f}s"
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {str(e)[:160]}"
        ms = int((time.perf_counter() - started) * 1000)
        logger.info(f"[health] {probe.name}: ok={ok} ms={ms} detail={detail[:120]!r}")
        return ProviderHealth(probe.name, ok, True, ms, detail)
