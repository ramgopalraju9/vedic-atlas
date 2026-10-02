from datetime import datetime
from typing import AsyncIterator

from domain.entities.agent_context import AgentContext
from domain.entities.agent_result import AgentResult
from domain.ports.inference_port import InferencePort
from domain.ports.knowledge_store_port import KnowledgeStorePort
from domain.ports.memory_repository_port import MemoryRepositoryPort
from service.agent.base_llm_agent import LLMAgent
from service.agent.persona import VEDA_SYSTEM_PROMPT
from service.agent.tool_calling import ToolCallingLoop
from service.conversation.conversation_manager import ConversationManager
from service.memory.semantic_recall import SemanticRecall


class ResponderAgent(LLMAgent):
    """General conversational agent wired to Veda's persona + persistent memory."""

    def __init__(
        self,
        client: InferencePort,
        conversation: ConversationManager,
        knowledge: KnowledgeStorePort,
        model: str | None = None,
        max_history_turns: int = 20,
        memory: MemoryRepositoryPort | None = None,
        recall: SemanticRecall | None = None,
        tool_loop: ToolCallingLoop | None = None,
    ):
        super().__init__(
            name="responder",
            description=(
                "General conversation, persona replies, Q&A, small talk, and anything "
                "the user wants to chat about. The safe default when no specialist fits."
            ),
            system_prompt=VEDA_SYSTEM_PROMPT,
            client=client,
            model=model,
            memory=memory,
        )
        self.conversation = conversation
        self.knowledge = knowledge
        self.max_history_turns = max_history_turns
        self._recall = recall
        self._tool_loop = tool_loop

    def build_system_prompt(self, ctx: AgentContext) -> str:
        # Persona only: byte-identical every turn so the backend's KV prefix
        # cache is reused. Volatile context lives in build_prompt - PERF_BRIEF §5.2.
        return VEDA_SYSTEM_PROMPT

    @staticmethod
    def _time_context() -> str:
        now = datetime.now()
        hour = now.hour
        if hour < 5:
            part = "late night"
        elif hour < 12:
            part = "morning"
        elif hour < 17:
            part = "afternoon"
        elif hour < 21:
            part = "evening"
        else:
            part = "night"
        return f"CURRENT TIME: {now.strftime('%H:%M on %A')} ({part})."

    def build_prompt(self, ctx: AgentContext) -> str:
        parts: list[str] = []
        # Least-volatile context first so the cacheable prefix extends as far as
        # possible; every-turn blocks go last, right before the question.
        knowledge_block = ctx.metadata.get("semantic_knowledge_context") or self.knowledge.get_context()
        if knowledge_block:
            parts.append(knowledge_block)
        if ctx.system_context:
            parts.append(f"CURRENT CONTEXT:\n{ctx.system_context}")
        mem_ctx = self._memory_context()
        if mem_ctx:
            parts.append(mem_ctx)
        history_block = self._render_history()
        if history_block:
            parts.append(history_block)
        parts.append(self._time_context())
        if ctx.from_voice:
            parts.append(
                "DELIVERY: this request arrived via voice - keep the reply to one or two "
                "short sentences, zero lists, nothing the user wouldn't hear in one breath."
            )
        parts.append(f"USER: {ctx.user_message}")
        parts.append("Respond directly to the user. Be concise and conversational.")
        return "\n\n".join(parts)

    async def _prepare_semantic_context(self, ctx: AgentContext) -> None:
        if self._recall is None or not self._recall.enabled:
            return
        hits = await self._recall.recall(ctx.user_message)
        block = self._recall.as_context_block(hits)
        if block:
            ctx.metadata["semantic_knowledge_context"] = block

    async def execute(self, ctx: AgentContext) -> AgentResult:
        await self._prepare_semantic_context(ctx)
        if self._tool_loop is not None:
            self._mark_active(ctx)
            system = self.build_system_prompt(ctx)
            user = self.build_prompt(ctx)
            response = (await self._tool_loop.run(ctx, system, user)) or (
                "I couldn't complete that. Could you rephrase?"
            )
            await self.on_completion(ctx, response)
            return AgentResult(agent_name=self.name, response=response, skill_calls=list(ctx.skill_results))
        return await super().execute(ctx)

    async def execute_stream(self, ctx: AgentContext, cancel_event=None) -> AsyncIterator[str]:
        await self._prepare_semantic_context(ctx)
        if self._tool_loop is not None:
            # Tool-calling is multi-step, so it can't token-stream; run it and
            # emit the final answer in one chunk.
            result = await self.execute(ctx)
            yield result.response
            return
        async for chunk in super().execute_stream(ctx, cancel_event=cancel_event):
            yield chunk

    def _render_history(self) -> str:
        recent = self.conversation.turns[-self.max_history_turns:]
        if not recent:
            return ""
        lines = []
        for turn in recent:
            content = (turn.content or "").strip()
            if not content:
                continue
            prefix = "User" if turn.role == "user" else "Veda"
            lines.append(f"{prefix}: {content}")
        return "RECENT CONVERSATION:\n" + "\n".join(lines) if lines else ""

    async def on_completion(self, ctx: AgentContext, response: str) -> None:
        if not response:
            return
        self.conversation.add_turn("user", ctx.user_message)
        self.conversation.add_turn("assistant", response)
        self._record_to_memory(action="chat", context={}, user_message=ctx.user_message)