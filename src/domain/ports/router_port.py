"""RouterPort — picks the specialist agent for a message with a model.

The supervisor depends on this port, not on the concrete LLM router, so the routing model (a small dedicated
one, or the main model) can be swapped or switched off by configuration.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from domain.entities.route_decision import RouteDecision


@runtime_checkable
class RouterPort(Protocol):
    async def route(self, message: str, agents: Sequence[tuple[str, str]]) -> RouteDecision | None:
        """`agents` = (name, description) of every routable agent. None when the router cannot decide
        (timeout, error, paused) - the caller then falls back to the keyword rules."""
        ...

    def paused_for_sec(self) -> float:
        """Seconds left of the slow-device pause (0 = the router is active). While paused, route() returns None."""
        ...

    async def warmup(self, agents: Sequence[tuple[str, str]]) -> None:
        """Load the model's prompt prefix once at boot so the first real message is not the slow one."""
        ...
