"""AmbientDispatcher — decides how (and whether) to voice a proactive ambient event, and owns the proactivity level.

Extracted unchanged from SupervisorAgent, where it never belonged: it handles events from `service/sensing`, not
user turns, so it is not an agent. Donor: veda/agents/supervisor.py::dispatch_ambient / `_effective_rate_max`.

`dispatch_ambient` returns the text to voice/display verbatim (no paraphrasing), or None to suppress the event.
"""

from __future__ import annotations

from core.logging_config import logger
from domain.events.ambient_event import AmbientEvent
from domain.events.event_kind import EventKind
from domain.policies.proactivity_policy import rate_limit_multiplier
from domain.value_objects.proactivity_level import ProactivityLevel
from domain.value_objects.urgency import Urgency
from service.sensing.rate_limiter import Debouncer, RateLimiter

VALID_PROACTIVITY = ("conservative", "medium", "chatty")


class AmbientDispatcher:
    def __init__(
        self,
        *,
        debounce_window_sec: float = 30.0,
        rate_limit_max: int = 6,
        rate_limit_window_sec: float = 60.0,
        proactivity: str = "medium",
        conversation=None,
    ):
        self.conversation = conversation  # ConversationManager, optional — only to mirror ambient summaries into history
        self._rate_limit_max = rate_limit_max
        self._rate_limit_window_sec = rate_limit_window_sec
        self.proactivity = proactivity if proactivity in VALID_PROACTIVITY else "medium"
        self._debouncer = Debouncer(window_sec=debounce_window_sec)
        self._rate_limiter = RateLimiter(max_events=self._effective_rate_max(), window_sec=rate_limit_window_sec)

    def _effective_rate_max(self) -> int:
        return self._rate_limit_max * rate_limit_multiplier(ProactivityLevel(self.proactivity))

    def set_proactivity(self, level: str) -> str:
        if level not in VALID_PROACTIVITY:
            raise ValueError(f"invalid proactivity {level!r}; expected conservative|medium|chatty")
        self.proactivity = level
        self._rate_limiter = RateLimiter(max_events=self._effective_rate_max(), window_sec=self._rate_limit_window_sec)
        logger.info(f"ambient proactivity -> {level}")
        return level

    async def dispatch_ambient(self, event: AmbientEvent) -> str | None:
        if event.kind == EventKind.HEARTBEAT:
            return None
        # Proactivity gate for LOW events.
        if event.urgency == Urgency.LOW and self.proactivity != "chatty":
            return None
        # Conservative mode also drops NORMAL observations that aren't clearly notifications.
        if self.proactivity == "conservative" and event.urgency == Urgency.NORMAL and event.kind == EventKind.OBSERVATION:
            return None
        if not self._debouncer.should_emit(event.dedupe_key):
            logger.info(f"ambient dedup: suppressed {event.source}/{event.dedupe_key}")
            return None
        if event.urgency != Urgency.HIGH and not self._rate_limiter.allow():
            logger.info(f"ambient rate-limited: {event.source}/{event.event_id}")
            return None
        # Mirror summary-flagged notifications into dialog history so the next
        # user turn can resolve pronouns against what Veda just said in an
        # ambient bubble. Other ambient sources stay out of history — too noisy.
        if self.conversation is not None and event.kind == EventKind.NOTIFICATION and event.payload.get("is_summary"):
            try:
                self.conversation.add_turn("assistant", event.description)
            except Exception as e:
                logger.warning(f"ambient: failed to record summary in history: {e}")
        return event.description
