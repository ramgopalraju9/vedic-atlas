"""SupervisorAgent — the entry point for a user turn. It no longer routes: every turn goes to the AssistantOrchestrator.

Routing (keyword rules, the router model, per-agent tool decisions) was deleted: it picked an agent from the newest
message alone, so a follow-up lost its context (docs/10, Bug A), and a keyword regex could force a tool the model had
correctly declined (Bug B). One control decode now decides every turn; this class only keeps the turn bookkeeping
(`current_agent`, `agent_chain`) the callers and traces rely on.

The ambient-event path that used to live here is service/sensing/ambient_dispatcher.py.
Governance: the old `route_to_agent` check had no meaning without routing; per-tool governance runs in SkillRunner.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from domain.entities.agent_context import AgentContext
from domain.entities.agent_result import AgentResult
from service.agent.base_agent import BaseAgent


class SupervisorAgent(BaseAgent):
    def __init__(self, orchestrator: BaseAgent, model: str | None = None):
        super().__init__(name="supervisor", description="Entry point: hands every user turn to the orchestrator.", model=model)
        self.orchestrator = orchestrator

    async def execute(self, ctx: AgentContext) -> AgentResult:
        ctx.current_agent = self.name
        ctx.agent_chain.append(self.name)
        result = await self.orchestrator.execute(ctx)
        result.delegated_to = result.delegated_to or self.orchestrator.name
        return result

    async def execute_stream(self, ctx: AgentContext, cancel_event: asyncio.Event | None = None) -> AsyncIterator[str]:
        ctx.current_agent = self.name
        ctx.agent_chain.append(self.name)
        async for chunk in self.orchestrator.execute_stream(ctx, cancel_event=cancel_event):
            yield chunk
