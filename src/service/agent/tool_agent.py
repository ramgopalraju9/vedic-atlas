"""ToolAgent — a specialist agent that owns a set of tools (declared in manifests).

One instance per owning agent name in the manifests (e.g. "tasks", "lookup").
The supervisor routes to it deterministically using the manifests' trigger
patterns. It runs the staged ToolTurnRunner; when the model decides no tool is
needed, the turn is handed to the plain-chat fallback agent, and any
"I did X" claim the fallback makes is refused because no tool ran.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from core.logging_config import logger
from domain.entities.agent_context import AgentContext
from domain.entities.agent_result import AgentResult
from domain.entities.turn_trace import TurnTrace
from domain.policies.reply_policy import UNCONFIRMED_REPLY
from domain.ports.memory_repository_port import MemoryRepositoryPort
from domain.ports.trace_repository_port import TraceRepositoryPort
from service.agent.base_agent import BaseAgent
from service.agent.tool_turn_runner import TurnOutcome, ToolTurnRunner
from service.agent.tool_use_guard import ToolUseGuard
from service.session.session_state import SessionStateService

_UNCONFIRMED_REPLY = UNCONFIRMED_REPLY
_ERROR_REPLY = "Something went wrong on my end. Could you try again?"


class ToolAgent(BaseAgent):
    def __init__(
        self,
        *,
        name: str,
        description: str,
        tool_names: list[str],
        triggers: list[str],
        runner: ToolTurnRunner,
        fallback: BaseAgent,
        guard: ToolUseGuard,
        conversation=None,
        memory: MemoryRepositoryPort | None = None,
        model: str | None = None,
        traces: TraceRepositoryPort | None = None,
        session_state: SessionStateService | None = None,
    ):
        super().__init__(name=name, description=description, skills=tool_names, model=model, triggers=triggers)
        self._runner = runner
        self._fallback = fallback
        self._guard = guard
        self._conversation = conversation
        self._memory = memory
        self._traces = traces
        self._session_state = session_state

    def _resolve_session(self, ctx: AgentContext) -> None:
        """Resolve the turn's session id exactly once and keep it on the context, so state reads/writes and the
        saved turns all use the same id even if the 30-minute boundary is crossed mid-turn."""
        if ctx.session_id is None and self._conversation is not None:
            try:
                ctx.session_id = self._conversation.current_session_id()
            except Exception as e:
                logger.warning(f"[{self.name}] session id unavailable: {e}")

    def _shadow_log_active(self, ctx: AgentContext) -> None:
        """Phase 1 shadow mode: show what the ACTIVE: line WOULD be. It is not used anywhere."""
        if self._session_state is None or ctx.session_id is None:
            return
        line = self._session_state.active_line(ctx.session_id, ctx.speaker_id)
        if line:
            logger.info(f"[session-state] shadow {line} (session={ctx.session_id})")

    async def execute(self, ctx: AgentContext) -> AgentResult:
        ctx.current_agent = self.name
        if not ctx.agent_chain or ctx.agent_chain[-1] != self.name:
            ctx.agent_chain.append(self.name)

        self._resolve_session(ctx)
        self._shadow_log_active(ctx)
        started = time.perf_counter()
        try:
            outcome = await self._runner.run(ctx, self.skills)
        except Exception as e:  # never let a tool-stage bug take the turn down
            logger.exception(f"[{self.name}] tool turn failed: {e}")
            self._record_trace(ctx, TurnOutcome(reply=None, notes=[f"error: {e}"]), _ERROR_REPLY, started, "error")
            return AgentResult(agent_name=self.name, response=_ERROR_REPLY)

        if outcome.reply is None:
            result = await self._fallback.execute(ctx)
            if self._guard.claims_action(result.response):
                logger.warning(f"[tool-guard] chat reply claimed an action with no tool call: {result.response[:160]!r}")
                result.response = _UNCONFIRMED_REPLY
                outcome.notes.append("chat reply claimed an action; refused")
            self._record_trace(ctx, outcome, result.response, started, "no-tool")
            return result

        self._persist(ctx, outcome.reply)
        if self._session_state is not None and ctx.session_id is not None:
            self._session_state.record_success(ctx.session_id, ctx.speaker_id, outcome.calls)
        self._record_trace(ctx, outcome, outcome.reply, started, "tool")
        return AgentResult(agent_name=self.name, response=outcome.reply, skill_calls=list(ctx.skill_results))

    async def execute_stream(self, ctx: AgentContext, cancel_event: asyncio.Event | None = None) -> AsyncIterator[str]:
        # Tool turns can't token-stream (the reply exists only after the tool ran): emit once.
        result = await self.execute(ctx)
        yield result.response

    def _record_trace(self, ctx: AgentContext, outcome: TurnOutcome, reply: str, started: float, decided: str) -> None:
        if self._traces is None:
            return
        try:
            self._traces.record(TurnTrace(
                request_id=ctx.request_id, agent=self.name, user_message=ctx.user_message[:300],
                reply=reply[:400], decided=decided, forced=outcome.forced, narrated=outcome.narrated,
                total_ms=int((time.perf_counter() - started) * 1000),
                calls=[
                    {"tool": c.tool, "args": c.args, "ok": c.ok, "ms": c.ms,
                     "result": (c.observation or "")[:300], "error": c.error}
                    for c in outcome.calls
                ],
                prompt_tokens=outcome.prompt_tokens, timings_ms=outcome.timings_ms,
                notes=[f"routed via {ctx.metadata.get('routed_via', '?')}", *outcome.notes],
            ))
        except Exception as e:  # tracing must never break a turn
            logger.warning(f"[{self.name}] trace record failed: {e}")

    def _persist(self, ctx: AgentContext, reply: str) -> None:
        if self._conversation is not None:
            self._conversation.add_turn("user", ctx.user_message, session_id=ctx.session_id)
            self._conversation.add_turn("assistant", reply, session_id=ctx.session_id)
        if self._memory is not None:
            try:
                self._memory.record(agent_name=self.name, action="tool_turn", context={}, user_message=ctx.user_message)
            except Exception as e:
                logger.debug(f"[{self.name}] memory record failed: {e}")
