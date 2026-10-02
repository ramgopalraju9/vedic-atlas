"""HookRegistry — event dispatch system for cross-cutting concerns.

Donor: veda/hooks/registry.py, read in full and ported verbatim.
"""

from typing import Any, Awaitable, Callable

from core.enums import HookEvent
from core.logging_config import logger

HookHandler = Callable[..., Awaitable[bool | None]]


class HookRegistry:
    """Central registry for event hooks.

    Hooks fire at defined points in the agent/skill lifecycle. For pre_*
    events, returning False from any handler aborts the operation.
    """

    def __init__(self):
        self._hooks: dict[HookEvent, list[HookHandler]] = {event: [] for event in HookEvent}

    def register(self, event: HookEvent, handler: HookHandler) -> None:
        self._hooks[event].append(handler)
        logger.debug(f"Registered hook for {event.value}: {getattr(handler, '__name__', repr(handler))}")

    def register_many(self, event: HookEvent, handlers: list[HookHandler]) -> None:
        for handler in handlers:
            self.register(event, handler)

    async def fire(self, event: HookEvent, **kwargs: Any) -> list[bool | None]:
        """Fire all handlers for an event. For pre_* events, False aborts the chain."""
        results = []
        for handler in self._hooks[event]:
            try:
                result = await handler(**kwargs)
                results.append(result)
                if event.value.startswith("pre_") and result is False:
                    logger.info(f"Hook '{getattr(handler, '__name__', '?')}' aborted {event.value}")
                    break
            except Exception as e:
                logger.error(f"Hook error in {event.value}: {e}")
                if event.value.startswith("pre_"):
                    raise
                results.append(None)
        return results

    def get_handler_count(self, event: HookEvent) -> int:
        return len(self._hooks[event])

    @property
    def total_handlers(self) -> int:
        return sum(len(handlers) for handlers in self._hooks.values())