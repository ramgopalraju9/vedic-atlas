"""LLMAgent - concrete agent backed by an InferencePort.

Donor: veda/agents/llm_agent.py, read in full and adapted:
- Depends on InferencePort (domain.ports) instead of the donor's
  concrete `LLMClient`.
- Depends on MemoryRepositoryPort instead of a concrete
  `AgentMemoryRepository` - the memory-context helpers
  (`_memory_context`, `_record_to_memory`) call the port's real methods
  (`recent_cross_agent`, `record`) exactly as the donor did.
- `execute()` no longer passes `image_paths` to the inference call -
  vision is out of scope; InferencePort has no such parameter.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from domain.entities.agent_context import AgentContext
from domain.entities.agent_result import AgentResult
from domain.ports.inference_port import InferencePort
from domain.ports.memory_repository_port import MemoryRepositoryPort
from service.agent.base_agent import BaseAgent
from core.logging_config import logger


class LLMAgent(BaseAgent):
    """Agent that assembles a prompt, calls the local inference backend, and returns text.

    Shape:
        build_system_prompt(ctx) -> str      persona + persistent context
        build_prompt(ctx)        -> str      history + current user turn
        on_completion(ctx, text) -> None     subclass persistence hook
    """

    def __init__(
        self,
        name: str,
        description: str,
        system_prompt: str,
        client: InferencePort,
        model: str | None = None,
        skills: list[str] | None = None,
        memory: MemoryRepositoryPort | None = None,
        voice_num_predict: int | None = 120,
    ):
        super().__init__(name, description, skills, system_prompt, model)
        self.client = client
        self.memory = memory
        self.voice_num_predict = voice_num_predict

    # -- Memory helpers ---------------------------------------------------

    def _memory_context(self, *, exclude_self: bool = True) -> str:
        """Format recent cross-agent activity as a prompt section.

        Returns an empty string when memory is unavailable or empty.
        """
        if self.memory is None:
            return ""
        try:
            exclude = {self.name} if exclude_self else None
            recent = self.memory.recent_cross_agent(exclude=exclude, limit=5)
            if not recent:
                return ""
            lines = []
            for m in recent:
                ctx_str = ", ".join(f"{k}={v}" for k, v in m.context.items()) if m.context else ""
                ts = m.created_at.isoformat() if m.created_at else ""
                lines.append(
                    f"- [{m.agent_name}] {m.action}"
                    + (f" ({ctx_str})" if ctx_str else "")
                    + (f" @ {ts}" if ts else "")
                )
            return "RECENT AGENT ACTIVITY:\n" + "\n".join(lines)
        except Exception as e:
            logger.debug(f"[{self.name}] memory context read failed: {e}")
            return ""

    def _record_to_memory(
        self, action: str, context: dict | None = None, user_message: str = ""
    ) -> None:
        """Record an action to the shared cross-agent memory mesh."""
        if self.memory is None:
            return
        try:
            self.memory.record(
                agent_name=self.name,
                action=action,
                context=context or {},
                user_message=user_message,
            )
        except Exception as e:
            logger.debug(f"[{self.name}] memory record failed: {e}")

    # -- Subclass extension points -----------------------------------------

    def build_system_prompt(self, ctx: AgentContext) -> str:
        return self.system_prompt

    def build_prompt(self, ctx: AgentContext) -> str:
        """Assemble the prompt sent to the model. Default: just the user message."""
        if ctx.system_context:
            return f"[CONTEXT]\n{ctx.system_context}\n\nUSER: {ctx.user_message}"
        return ctx.user_message

    async def on_completion(self, ctx: AgentContext, response: str) -> None:
        """Hook called once after a successful execute. Default: records to memory."""
        self._record_to_memory(action="complete", context={}, user_message=ctx.user_message)

    # -- Execute -----------------------------------------------------------

    def _budget(self, ctx: AgentContext) -> dict:
        """Generation cap for this turn.

        A spoken reply has to be short, and on CPU every extra token is real
        wall-clock delay before the user hears anything - so voice turns get a
        much tighter budget than typed ones.
        """
        if ctx.from_voice and self.voice_num_predict:
            return {"num_predict": self.voice_num_predict}
        return {}

    def _mark_active(self, ctx: AgentContext) -> None:
        ctx.current_agent = self.name
        if not ctx.agent_chain or ctx.agent_chain[-1] != self.name:
            ctx.agent_chain.append(self.name)

    async def execute(self, ctx: AgentContext) -> AgentResult:
        self._mark_active(ctx)
        system = self.build_system_prompt(ctx)
        prompt = self.build_prompt(ctx)

        try:
            response = await self.client.complete(
                prompt=prompt, system=system, model=self.model,
                **self._budget(ctx),
            )
        except Exception as e:
            logger.error(f"Agent '{self.name}' inference call failed: {e}")
            response = "Something went wrong on my end. Let me know if you'd like to try again."

        if not response:
            response = (
                "I'm having trouble reaching my local model right now. "
                "Could you try again in a moment?"
            )

        await self.on_completion(ctx, response)
        return AgentResult(agent_name=self.name, response=response, skill_calls=list(ctx.skill_results))

    async def execute_stream(
        self, ctx: AgentContext, cancel_event: asyncio.Event | None = None
    ) -> AsyncIterator[str]:
        """Stream a response, chunk by chunk.

        Correction (2026-09-22): the first pass at this method (built
        before veda/agents/llm_agent.py's execute_stream was read to its
        real end at line 185, not the 158 originally seen) unconditionally
        overwrote any partial output with a canned fallback message on
        error. Fixed to match the donor exactly: the fallback is only
        substituted if NOTHING streamed yet; partial output that already
        reached the caller is preserved and recorded as-is. Also matches
        the donor's cancel_event guard around the final on_completion call.
        """
        self._mark_active(ctx)
        system = self.build_system_prompt(ctx)
        prompt = self.build_prompt(ctx)
        full = ""
        try:
            async for chunk in self.client.stream(
                prompt=prompt, system=system, model=self.model, cancel_event=cancel_event,
                **self._budget(ctx),
            ):
                if cancel_event is not None and cancel_event.is_set():
                    break
                if chunk:
                    full += chunk
                    yield chunk
        except Exception as e:
            logger.error(f"Agent '{self.name}' streaming inference call failed: {e}")
            if not full:
                fallback = "Something went wrong on my end. Let me know if you'd like to try again."
                yield fallback
                full = fallback

        if not (cancel_event is not None and cancel_event.is_set()):
            await self.on_completion(ctx, full.strip())