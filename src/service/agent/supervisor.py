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

The ambient-event path (`dispatch_ambient`, proactivity, debounce/rate limit) used to live here; it moved
unchanged to service/sensing/ambient_dispatcher.py, since it never handled a user turn.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from domain.entities.agent_context import AgentContext
from domain.entities.agent_profile import AgentProfile
from domain.entities.agent_result import AgentResult
from domain.ports.governance_port import GovernanceProvider
from domain.ports.inference_port import InferencePort
from domain.ports.memory_repository_port import MemoryRepositoryPort
from domain.ports.router_port import RouterPort
from domain.policies.routing_policy import is_confident, match_agent_detail
from service.agent.base_agent import BaseAgent
from service.agent.registry import AgentRegistry
from service.agent.router_policy import RouterPolicy
from core.logging_config import logger



class SupervisorAgent(BaseAgent):
    """Routes to a single sub-agent per request."""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        client: InferencePort,
        default_agent: str = "responder",
        model: str | None = None,
        memory: MemoryRepositoryPort | None = None,
        governance: GovernanceProvider | None = None,
        router: RouterPolicy | None = None,
        routing_num_predict: int = 64,
        llm_router: RouterPort | None = None,
        routing_mode: str | None = None,
        router_on_failure: str = "keyword",
        orchestrator: BaseAgent | None = None,
    ):
        super().__init__(name="supervisor", description="Routes user requests to the correct specialist agent.", model=model)
        self.agent_registry = agent_registry
        self.client = client
        self.default_agent = default_agent
        self.memory = memory
        self.governance = governance
        self.router = router or RouterPolicy()
        self.routing_num_predict = routing_num_predict
        # When set, EVERY turn goes to the orchestrator and none of the routing below runs (docs/10).
        self.orchestrator = orchestrator
        self.llm_router = llm_router  # RouterPort | None — the model that routes (see routing_mode)
        # keyword | hybrid | model (config/routing.yaml). None = the legacy behaviour: rules first, and the model
        # only for a message no rule recognised (what `agents.llm_routing: true` always did).
        self.routing_mode = routing_mode
        self.router_on_failure = router_on_failure if router_on_failure in {"keyword", "chat"} else "keyword"

    # -- Routing ---------------------------------------------------------

    async def _pick(self, ctx: AgentContext) -> BaseAgent:
        """Choose the specialist for a message, by `routing_mode` (config/routing.yaml):

        keyword - the manifest trigger rules and word overlap only; no model call.
        model   - the router model decides every message; the keyword rules are the fallback if it cannot answer.
        hybrid  - the keyword rules decide when SURE (one agent's trigger matched, no correction cue); the router
                  model decides everything else.
        None    - legacy: rules first, the router model only for a message no rule recognised.
        """
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
        agents = [(a.name, a.description) for a in routable]
        mode = self.routing_mode
        ctx.metadata["routing_mode"] = mode or "legacy"

        if mode == "model":
            routed = await self._route_by_model(ctx, agents)
            return routed or self._route_after_model_failure(ctx, profiles)

        if mode == "hybrid":
            detail = match_agent_detail(ctx.user_message, profiles)
            if is_confident(detail) and self.agent_registry.is_registered(detail.agent):
                logger.info(f"[route] mode=hybrid via=rules(sure) agent={detail.agent}")
                ctx.metadata["routed_via"] = "rules"
                return self.agent_registry.get(detail.agent)
            routed = await self._route_by_model(ctx, agents)
            return routed or self._route_after_model_failure(ctx, profiles)

        chosen = self.router.match(ctx.user_message, profiles)
        if chosen and self.agent_registry.is_registered(chosen):
            logger.info(f"Supervisor rule-routed to '{chosen}'")
            ctx.metadata["routed_via"] = "rules"
            return self.agent_registry.get(chosen)

        if mode is None:  # legacy
            routed = await self._route_by_model(ctx, agents)
            if routed is not None:
                return routed

        ctx.metadata["routed_via"] = "default"
        return self.agent_registry.get(self.default_agent)

    async def _route_by_model(self, ctx: AgentContext, agents: list[tuple[str, str]]) -> BaseAgent | None:
        """The router model's pick, or None when there is no router or it could not answer."""
        if self.llm_router is None:
            return None
        decision = await self.llm_router.route(ctx.user_message, agents)
        if decision is None or not self.agent_registry.is_registered(decision.agent):
            return None
        ctx.metadata["routed_via"] = f"llm ({decision.ms} ms)"
        logger.info(f"[route] mode={ctx.metadata.get('routing_mode')} via=llm agent={decision.agent} ms={decision.ms}")
        return self.agent_registry.get(decision.agent)

    def _route_after_model_failure(self, ctx: AgentContext, profiles: tuple[AgentProfile, ...]) -> BaseAgent:
        """The router model gave no answer: use the keyword rules (or chat), as `routing.on_failure` says."""
        if self.router_on_failure == "keyword":
            chosen = self.router.match(ctx.user_message, profiles)
            if chosen and self.agent_registry.is_registered(chosen):
                logger.warning(f"[route] router model gave no answer; keyword rules -> '{chosen}'")
                ctx.metadata["routed_via"] = "fallback-rules"
                return self.agent_registry.get(chosen)
        logger.warning(f"[route] router model gave no answer; default agent '{self.default_agent}'")
        ctx.metadata["routed_via"] = "fallback-default"
        return self.agent_registry.get(self.default_agent)

    def routing_status(self) -> dict:
        """What routing is doing right now, for /api/health: the mode, whether a router model is loaded, and whether
        its slow-device pause is active (in which case every message is being routed by the keyword fallback)."""
        paused = self.llm_router.paused_for_sec() if self.llm_router is not None else 0.0
        return {
            "mode": self.routing_mode or "legacy",
            "router_model": self.llm_router is not None,
            "router_paused_for_sec": int(paused),
        }

    async def warm_router(self) -> None:
        """Warm the router model's prompt cache at boot (no-op without a router)."""
        if self.llm_router is None:
            return
        routable = self.agent_registry.list_routable(exclude={self.name})
        await self.llm_router.warmup([(a.name, a.description) for a in routable])

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
        if self.orchestrator is not None:
            result = await self.orchestrator.execute(ctx)
            result.delegated_to = result.delegated_to or self.orchestrator.name
            return result
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
        if self.orchestrator is not None:
            async for chunk in self.orchestrator.execute_stream(ctx, cancel_event=cancel_event):
                yield chunk
            return
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
