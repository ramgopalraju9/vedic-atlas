"""CircuitBreaker — per-backend health tracking for inference calls.

★ new (FILE_MAP.md explicitly calls this out as its own module). Donor:
veda/governance/builtin.py's `_BreakerState` dataclass + the
is_healthy/record_success/record_failure trio, previously inlined
directly on `BuiltinProvider`. Extracted so `PolicyEvaluator` composes it
rather than mixing policy-rule evaluation and breaker bookkeeping in one
class.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from core.logging_config import logger


@dataclass
class _BreakerState:
    failures: int = 0
    tripped: bool = False
    last_failure: float = 0.0
    trip_time: float = 0.0


class CircuitBreaker:
    """Trips a backend "unhealthy" after N consecutive failures; half-opens after a timeout."""

    def __init__(self, threshold: int = 3, timeout_sec: float = 60.0):
        self._threshold = threshold
        self._timeout_sec = timeout_sec
        self._states: dict[str, _BreakerState] = {}

    def is_healthy(self, backend: str) -> bool:
        state = self._states.get(backend)
        if state is None or not state.tripped:
            return True
        if time.time() - state.trip_time >= self._timeout_sec:
            return True  # half-open: let the next call attempt through
        return False

    def record_success(self, backend: str) -> None:
        state = self._states.get(backend)
        if state is None:
            return
        state.failures = 0
        state.tripped = False

    def record_failure(self, backend: str) -> None:
        state = self._states.setdefault(backend, _BreakerState())
        state.failures += 1
        state.last_failure = time.time()
        if state.failures >= self._threshold:
            state.tripped = True
            state.trip_time = time.time()
            logger.warning(f"[governance] circuit breaker TRIPPED for '{backend}' ({state.failures} failures)")

    @property
    def states(self) -> dict[str, dict]:
        return {
            name: {"failures": s.failures, "tripped": s.tripped, "last_failure": s.last_failure}
            for name, s in self._states.items()
        }