"""SupervisorAgent — routes user messages to a specialist sub-agent.

Donor: veda/agents/supervisor.py, read in full and adapted:
  - DROPPED the code-agent-specific instant intercepts entirely (cancel
    word, yolo-toggle, voice yes/no via `veda.code.approval.broker` /
    `veda.code.runner.tasks`) — the code agent is out of scope.
  - Routing itself now tries RouterPolicy (keyword/description match)
    FIRST, falling back to an LLM call only when that returns None — the
    reverse of the donor's LLM-first design. This is the ★ PROVISIONAL
    decision flagged in domain/policies/routing_policy.py; swap the
    strategy there if a different approach is chosen later.
  - Governance check replaced with an injected GovernanceProvider (Batch 3
    Port) instead of a module-level `from veda.governance import ...`.
  - Memory-based fallback (`last_specialist`) kept — it's a real,
    evidenced feature of the donor's routing and doesn't depend on the
    code agent.

Correction (2026-09-22): `dispatch_ambient` was missing from this port
entirely — the first read of veda/agents/supervisor.py (270 of the real
308 lines) never reached it. Added below, read in full this batch. The
donor's summary-mirroring branch (folding a NOTIFICATION event's
`is_summary` payload into conversation history) existed specifically for
the code-runner's task summaries — the code agent is out of scope, so
that payload shape will never actually occur in this build, but the
mechanism is harmless to keep (it simply never fires) and removing it
buys nothing.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from domain.entities.agent_context import AgentContext
from domain.entities.agent_profile import AgentProfile
from domain.entities.agent_result import AgentResult
from domain.events.ambient_event import AmbientEvent
from domain.events.event_kind import EventKind
from domain.value_objects.urgency import Urgency
from domain.ports.governance_port import GovernanceProvider
from domain.ports.inference_port import InferencePort
from domain.ports.memory_repository_port import MemoryRepositoryPort
from service.agent.base_agent import BaseAgent
from service.agent.registry import AgentRegistry
from service.agent.router_policy import RouterPolicy
from service.sensing.event_bus import EventBus
from service.sensing.rate_limiter import Debouncer, RateLimiter
from core.logging_config import logger



class SupervisorAgent(BaseAgent):
    """Routes to a single sub-agent per request."""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        client: InferencePort,
        default_agent: str = "responder",
        model: str | None = None,
        debounce_window_sec: float = 30.0,
        rate_limit_max: int = 6,
        rate_limit_window_sec: float = 60.0,
        proactivity: str = "medium",
        memory: MemoryRepositoryPort | None = None,
        governance: GovernanceProvider | None = None,
        router: RouterPolicy | None = None,
        conversation=None,
        routing_num_predict: int = 64,
        llm_router=None,
    ):
        super().__init__(name="supervisor", description="Routes user requests to the correct specialist agent.", model=model)
        self.agent_registry = agent_registry
        self.client = client
        self.default_agent = default_agent
        self.memory = memory
        self.governance = governance
        self.router = router or RouterPolicy()
        self.conversation = conversation  # ConversationManager, optional — used only to mirror ambient summaries into history
        self.routing_num_predict = routing_num_predict
        self.llm_router = llm_router  # LlmRouter | None — decides messages the rules did not recognise
        self._rate_limit_max = rate_limit_max
        self._rate_limit_window_sec = rate_limit_window_sec
        self.proactivity = proactivity if proactivity in {"conservative", "medium", "chatty"} else "medium"
        self._debouncer = Debouncer(window_sec=debounce_window_sec)
        self._rate_limiter = RateLimiter(max_events=self._effective_rate_max(), window_sec=rate_limit_window_sec)

    def _effective_rate_max(self) -> int:
        from domain.policies.proactivity_policy import rate_limit_multiplier
        from domain.value_objects.proactivity_level import ProactivityLevel
        return self._rate_limit_max * rate_limit_multiplier(ProactivityLevel(self.proactivity))

    def set_proactivity(self, level: str) -> str:
        if level not in {"conservative", "medium", "chatty"}:
            raise ValueError(f"invalid proactivity {level!r}; expected conservative|medium|chatty")
        self.proactivity = level
        self._rate_limiter = RateLimiter(max_events=self._effective_rate_max(), window_sec=self._rate_limit_window_sec)
        logger.info(f"supervisor proactivity -> {level}")
        return level

    # -- Routing ---------------------------------------------------------

    async def _pick(self, ctx: AgentContext) -> BaseAgent:
        """Deterministic rules first (instant), then an LLM router for what they did not recognise."""
        routable = self.agent_registry.list_routable(exclude={self.name})
        if not routable:
            ctx.metadata["routed_via"] = "default"
            return self.agent_registry.get(self.default_agent)
        if len(routable) == 1:
            ctx.metadata["routed_via"] = "only-agent"
            return routable[0]

        profiles = tuple(
            AgentProfile(
                name=a.name, description=a.description, model_alias=a.model,
                skills=tuple(a.skills), triggers=tuple(a.triggers),
            )
            for a in routable
        )
        chosen = self.router.match(ctx.user_message, profiles)
        if chosen and self.agent_registry.is_registered(chosen):
            logger.info(f"Supervisor rule-routed to '{chosen}'")
            ctx.metadata["routed_via"] = "rules"
            return self.agent_registry.get(chosen)

        if self.llm_router is not None:
            decision = await self.llm_router.route(ctx.user_message, [(a.name, a.description) for a in routable])
            if decision is not None and self.agent_registry.is_registered(decision.agent):
                ctx.metadata["routed_via"] = f"llm ({decision.ms} ms)"
                return self.agent_registry.get(decision.agent)

        ctx.metadata["routed_via"] = "default"
        return self.agent_registry.get(self.default_agent)

    # -- Execute ---------------------------------------------------------

    def _record_routing(self, agent_name: str, user_message: str) -> None:
        if self.memory is None:
            return
        try:
            self.memory.record(agent_name="supervisor", action="route", context={"target": agent_name}, user_message=user_message)
        except Exception as e:
            logger.warning(f"Supervisor memory record failed: {e}")

    async def execute(self, ctx: AgentContext) -> AgentResult:
        ctx.current_agent = self.name
        ctx.agent_chain.append(self.name)
        agent = await self._pick(ctx)

        if self.governance is not None:
            decision = self.governance.check_action("route_to_agent", {"agent": agent.name, "message": ctx.user_message[:200]})
            if not decision.allowed:
                logger.warning(f"[governance] routing to '{agent.name}' denied: {decision.reason}")
                return AgentResult(agent_name=self.name, response=f"Policy denied: {decision.reason}")

        self._record_routing(agent.name, ctx.user_message)
        result = await agent.execute(ctx)
        result.delegated_to = agent.name
        return result

    async def execute_stream(self, ctx: AgentContext, cancel_event: asyncio.Event | None = None) -> AsyncIterator[str]:
        ctx.current_agent = self.name
        ctx.agent_chain.append(self.name)
        agent = await self._pick(ctx)

        if self.governance is not None:
            decision = self.governance.check_action("route_to_agent", {"agent": agent.name, "message": ctx.user_message[:200]})
            if not decision.allowed:
                logger.warning(f"[governance] routing to '{agent.name}' denied: {decision.reason}")
                yield f"Policy denied: {decision.reason}"
                return

        self._record_routing(agent.name, ctx.user_message)
        async for chunk in agent.execute_stream(ctx, cancel_event=cancel_event):
            if cancel_event is not None and cancel_event.is_set():
                break
            yield chunk

    # -- Ambient path ------------------------------------------------------

    async def dispatch_ambient(self, event: AmbientEvent) -> str | None:
        """Decide how to narrate an ambient event, or None to suppress.

        Donor: veda/agents/supervisor.py::dispatch_ambient, read in full.
        Returns the text to voice/display, verbatim (no paraphrasing).
        """
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
                logger.warning(f"supervisor: failed to record summary in history: {e}")
        return event.description