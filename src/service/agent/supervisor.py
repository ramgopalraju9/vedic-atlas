from __future__ import annotations

import asyncio
import json
import re
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

_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)
_ROUTER_SYSTEM_TEMPLATE = """You are a router for a personal assistant named Veda.
You never answer the user directly. Your only job is to pick the best specialist.

Available agents:
{agents}

Respond with EXACTLY one line of JSON, no markdown, no explanation:
{{"agent": "<agent_name>", "reason": "<one short sentence>"}}

If no specialist clearly fits, pick "{default}"."""


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
    ):
        super().__init__(name="supervisor", description="Routes user requests to the correct specialist agent.", model=model)
        self.agent_registry = agent_registry
        self.client = client
        self.default_agent = default_agent
        self.memory = memory
        self.governance = governance
        self.router = router or RouterPolicy()
        self.conversation = conversation  # ConversationManager, optional - used only to mirror ambient summaries into history
        self.routing_num_predict = routing_num_predict
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

    # -- Routing --------------------------------------------------------

    def _router_system_prompt(self) -> str:
        lines = [f"- {a.name}: {a.description}" for a in self.agent_registry.list_routable(exclude={self.name})]
        return _ROUTER_SYSTEM_TEMPLATE.format(agents="\n".join(lines), default=self.default_agent)

    async def _pick(self, ctx: AgentContext) -> BaseAgent:
        routable = self.agent_registry.list_routable(exclude={self.name})
        if not routable:
            return self.agent_registry.get(self.default_agent)
        if len(routable) == 1:
            return routable[0]

        profiles = tuple(
            AgentProfile(name=a.name, description=a.description, model_alias=a.model, skills=tuple(a.skills))
            for a in routable
        )
        chosen = self.router.pick(ctx.user_message, profiles, self.default_agent)
        if chosen and self.agent_registry.is_registered(chosen):
            logger.info(f"Supervisor keyword-routed to '{chosen}'")
            return self.agent_registry.get(chosen)

        # Fallback: LLM router (mirrors the donor's default path, now the fallback)
        system_prompt = self._router_system_prompt()
        try:
            raw = await self.client.complete(
                prompt=ctx.user_message,
                system=system_prompt,
                model=self.model,
                num_predict=self.routing_num_predict,
            )
            parsed = self._parse_agent_choice(raw)
            if parsed and self.agent_registry.is_registered(parsed):
                logger.info(f"Supervisor LLM-routed to '{parsed}' (raw={raw[:120]!r})")
                return self.agent_registry.get(parsed)
            logger.warning(f"Supervisor LLM routing failed or unknown agent (chosen={parsed!r}); falling back")
        except Exception as e:
            logger.error(f"Supervisor LLM routing call failed: {e}; falling back")

        if self.memory is not None:
            try:
                last = self.memory.last_action(agent_name="supervisor")
                target = (last.context or {}).get("target") if last else None
                if target and target != self.default_agent and self.agent_registry.is_registered(target):
                    logger.info(f"Supervisor memory-routed to '{target}' (LLM failed, last specialist)")
                    return self.agent_registry.get(target)
            except Exception as e:
                logger.warning(f"Supervisor memory lookup failed: {e}")

        return self.agent_registry.get(self.default_agent)

    @staticmethod
    def _parse_agent_choice(raw: str) -> str | None:
        if not raw:
            return None
        text = _JSON_FENCE_RE.sub("", raw).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        agent = data.get("agent")
        return agent.strip() if isinstance(agent, str) else None

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

    # -- Ambient path ----------------------------------------------------

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
        # ambient bubble. Other ambient sources stay out of history - too noisy.
        if self.conversation is not None and event.kind == EventKind.NOTIFICATION and event.payload.get("is_summary"):
            try:
                self.conversation.add_turn("assistant", event.description)
            except Exception as e:
                logger.warning(f"supervisor: failed to record summary in history: {e}")

        return event.description