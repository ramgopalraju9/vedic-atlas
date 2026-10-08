"""AssistantOrchestrator — one context-aware decision per turn, then execute (docs/10 §3.1).

    1  resolve the session id ONCE, read session state -> `ACTIVE:` line
    2  ONE control decode  ->  ControlDecision                      (ControlDecoder)
    3  dispatch_policy.resolve:  TOOLS | CLARIFY | CHAT | REFUSE | FAIL_CLOSED
    4  TOOLS   -> each call through SkillRunner (permission / rate-limit / validation / audit all apply)
                  -> reply_policy: template sentences verbatim, ONE content decode only for results that need narrating
       CHAT    -> ResponderAgent (persona, saved facts, summaries, recall; its reply veto is the claims backstop)
       others  -> a fixed sentence, no further model call
    5  claims backstop, persist the turn, write session state, record the TurnTrace

No regex reads the user's message here (principle P1); every decision above came from the model or from policy.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import AsyncIterator, Callable, Mapping, Sequence

from core.logging_config import logger
from domain.entities.agent_context import AgentContext
from domain.entities.agent_result import AgentResult
from domain.entities.control_decision import ControlDecision
from domain.entities.session_context import SessionContext
from domain.entities.tool_manifest import RETURNS_DOCUMENT, ToolManifest
from domain.entities.turn_trace import TurnTrace
from domain.policies.dispatch_policy import Resolution, Route, resolve
from domain.policies.grounding_policy import is_grounded
from domain.policies.reply_policy import (
    FAILED, NARRATE, SPOKEN, UNCONFIRMED_REPLY, classify_call, failure_text, spoken_text,
)
from domain.ports.memory_repository_port import MemoryRepositoryPort
from domain.ports.trace_repository_port import TraceRepositoryPort
from service.agent.base_agent import BaseAgent
from service.agent.control_decoder import ControlDecoder, DecodeResult
from service.agent.tool_execution import ExecutedCall, TurnOutcome, execute_call, narrate_with_grounding
from service.prompting.prompt_composer import PromptComposer
from service.session.session_state import SessionStateService
from service.skills.skill_runner import SkillRunner

_ERROR_REPLY = "Something went wrong on my end. Could you try again?"
_DOCUMENT_FALLBACK_TOKENS = 60  # about two spoken sentences: all a failed narration of a document may fall back to
_DECIDED = {
    Route.TOOLS: "tool", Route.CHAT: "chat", Route.CLARIFY: "clarify", Route.REFUSE: "refuse", Route.FAIL_CLOSED: "fail",
}


class AssistantOrchestrator(BaseAgent):
    def __init__(
        self,
        *,
        decoder: ControlDecoder,
        composer: PromptComposer,
        skill_runner: SkillRunner,
        manifests: Mapping[str, ToolManifest],
        responder: BaseAgent,
        client,
        conversation=None,
        claims_action: Callable[[str], bool] | None = None,
        session_state: SessionStateService | None = None,
        traces: TraceRepositoryPort | None = None,
        memory: MemoryRepositoryPort | None = None,
        model: str | None = None,
        narrate_num_predict: int = 90,
        narrate_temperature: float = 0.2,
        grounding_check: Callable[[str, Sequence[str], str], bool] | None = None,
    ):
        super().__init__(
            name="assistant", model=model,
            description="Decides each turn once: use a tool, ask a question, chat, or say it cannot look something up.",
        )
        self._decoder = decoder
        self._composer = composer
        self._skill_runner = skill_runner
        self._manifests = manifests
        self._responder = responder
        self._client = client
        self._conversation = conversation
        self._claims_action = claims_action
        self._session_state = session_state
        self._traces = traces
        self._memory = memory
        self._narrate_num_predict = narrate_num_predict
        self._narrate_temperature = narrate_temperature
        self._grounding_check = grounding_check or self._default_grounding

    @staticmethod
    def _default_grounding(reply: str, results: Sequence[str], user_message: str) -> bool:
        today = datetime.now().strftime("%A %d %B %Y %H:%M")
        return is_grounded(reply, [*results, user_message, today])

    # ---- turn preparation ---------------------------------------------------

    def _begin(self, ctx: AgentContext) -> None:
        ctx.current_agent = self.name
        if not ctx.agent_chain or ctx.agent_chain[-1] != self.name:
            ctx.agent_chain.append(self.name)
        if ctx.session_id is None and self._conversation is not None:   # resolved exactly once per turn
            try:
                ctx.session_id = self._conversation.current_session_id()
            except Exception as e:
                logger.warning(f"[assistant] session id unavailable: {e}")

    async def _decide(self, ctx: AgentContext) -> tuple[DecodeResult, Resolution, SessionContext | None, str, TurnOutcome]:
        state = None
        if self._session_state is not None and ctx.session_id is not None:
            state = self._session_state.get(ctx.session_id, ctx.speaker_id)
        active = self._session_state.render_active(state) if (self._session_state and state) else ""
        history = self._conversation.turns if self._conversation is not None else []
        result = await self._decoder.decide(ctx.user_message, history, active)
        resolution = resolve(result.decision, self._manifests, user_message=ctx.user_message)
        outcome = TurnOutcome(reply=None)
        outcome.timings_ms["control"] = result.ms
        outcome.prompt_tokens["control"] = result.prompt_tokens
        if result.trimmed:
            outcome.notes.append(f"control prompt trimmed: {list(result.trimmed)}")
        if not result.decision.valid:
            outcome.notes.append(f"control decision invalid: {result.decision.note}")
        logger.info(
            f"[control] route={resolution.route.value} live={result.decision.needs_live_data} "
            f"calls={[c.get('tool') for c in resolution.calls]} ms={result.ms} tokens={result.prompt_tokens} "
            f"active={active!r} user={ctx.user_message!r}"
        )
        return result, resolution, state, active, outcome

    # ---- execution ----------------------------------------------------------

    async def _run_tools(self, ctx: AgentContext, calls: Sequence[dict], outcome: TurnOutcome) -> str:
        t0 = time.perf_counter()
        for call in calls:
            outcome.calls.append(await execute_call(self._skill_runner, ctx, call, private=self._manifests[call["tool"]].private))
        outcome.timings_ms["execute"] = int((time.perf_counter() - t0) * 1000)

        t1 = time.perf_counter()
        reply = await self._assemble_reply(ctx, outcome)
        outcome.timings_ms["reply"] = int((time.perf_counter() - t1) * 1000)
        return reply

    async def _assemble_reply(self, ctx: AgentContext, outcome: TurnOutcome) -> str:
        """Template sentences are kept verbatim and never reach the model; only results that need narrating share
        ONE content decode, spliced in at the position of the first of them."""
        calls = outcome.calls
        kinds = [
            classify_call(ok=c.ok, reply_mode=self._manifests[c.tool].reply_mode, final=c.final) for c in calls
        ]
        narration: str | None = None
        to_narrate = [c for c, k in zip(calls, kinds) if k == NARRATE]
        if to_narrate:
            results = [self._composer.cap_tokens(c.observation, self._manifests[c.tool].max_result_tokens) for c in to_narrate]
            narration = await narrate_with_grounding(
                client=self._client, composer=self._composer, grounding_check=self._grounding_check, ctx=ctx,
                results=results, model=self.model, num_predict=self._narrate_num_predict,
                temperature=self._narrate_temperature, outcome=outcome,
            )
        pieces: list[str] = []
        narration_placed = False
        for c, kind in zip(calls, kinds):
            if kind == FAILED:
                # The skill's own sentence when it gave one ("Which app do you mean?", "I can't check your email yet because
                # it isn't set up"); the generic text with the raw error only for a failure that came with no sentence.
                pieces.append(c.spoken or failure_text(c.error))
            elif kind == SPOKEN:
                pieces.append(spoken_text(c.spoken, c.observation))
            elif narration is not None:
                if not narration_placed:
                    pieces.append(narration)
                    narration_placed = True
            else:  # narration failed or was ungrounded: say plainly what the tool returned
                text = spoken_text(c.spoken, c.observation)
                if self._manifests[c.tool].returns == RETURNS_DOCUMENT:
                    # A document's first line can be the whole document. This text is spoken AND saved into history,
                    # which feeds later prompts, so it is cut: a document must not leak into any control prompt.
                    text = self._composer.cap_tokens(text, _DOCUMENT_FALLBACK_TOKENS)
                pieces.append(text)
        return " ".join(pieces)

    def _backstop(self, reply: str, outcome: TurnOutcome) -> str:
        """claims_action on EVERY reply path. A claim is only legitimate if a tool actually succeeded this turn."""
        if self._claims_action is not None and not any(c.ok for c in outcome.calls) and self._claims_action(reply):
            logger.warning(f"[tool-guard] reply claimed an action with no successful tool call: {reply[:160]!r}")
            outcome.notes.append("reply claimed an action; refused")
            return UNCONFIRMED_REPLY
        return reply

    # ---- persistence & trace ------------------------------------------------

    def _persist(self, ctx: AgentContext, reply: str) -> None:
        if self._conversation is not None:
            self._conversation.add_turn("user", ctx.user_message, session_id=ctx.session_id)
            self._conversation.add_turn("assistant", reply, session_id=ctx.session_id)
        if self._memory is not None:
            try:
                self._memory.record(agent_name=self.name, action="turn", context={}, user_message=ctx.user_message)
            except Exception as e:
                logger.debug(f"[assistant] memory record failed: {e}")

    def _record_trace(
        self, ctx: AgentContext, route: Route, decision: ControlDecision, outcome: TurnOutcome, reply: str,
        started: float, state: SessionContext | None, active: str,
    ) -> None:
        if self._traces is None:
            return
        inherited = sorted({
            k for c in outcome.calls for k, v in c.args.items() if state is not None and state.slots.get(k) == v
        })
        try:
            self._traces.record(TurnTrace(
                request_id=ctx.request_id, agent=self.name, user_message=ctx.user_message[:300], reply=reply[:400],
                decided=_DECIDED[route], narrated=outcome.narrated, total_ms=int((time.perf_counter() - started) * 1000),
                calls=[
                    {"tool": c.tool, "args": c.args, "ok": c.ok, "ms": c.ms, "result": "" if self._manifests[c.tool].private else (c.observation or "")[:300], "error": c.error}
                    for c in outcome.calls
                ],
                prompt_tokens=outcome.prompt_tokens, timings_ms=outcome.timings_ms, notes=list(outcome.notes),
                state_used=bool(active), slots_inherited=inherited, needs_live_data=decision.needs_live_data,
                clarified=route is Route.CLARIFY,
            ))
        except Exception as e:  # tracing must never break a turn
            logger.warning(f"[assistant] trace record failed: {e}")

    def _write_state(self, ctx: AgentContext, outcome: TurnOutcome) -> None:
        if self._session_state is not None and ctx.session_id is not None and outcome.calls:
            self._session_state.record_success(ctx.session_id, ctx.speaker_id, outcome.calls)

    # ---- public -------------------------------------------------------------

    async def warmup(self) -> None:
        """One throw-away decode so the control prefix is already in the prompt cache for the first real turn."""
        try:
            await self._decoder.decide("hello", (), "")
            logger.info("[assistant] control prefix warmed")
        except Exception as e:
            logger.warning(f"[assistant] warmup skipped: {e}")


    async def execute(self, ctx: AgentContext) -> AgentResult:
        self._begin(ctx)
        started = time.perf_counter()
        try:
            result, res, state, active, outcome = await self._decide(ctx)
            if res.route is Route.CHAT:
                chat = await self._responder.execute(ctx)
                self._record_trace(ctx, res.route, result.decision, outcome, chat.response, started, state, active)
                chat.delegated_to = self._responder.name
                return chat
            reply = await self._run_tools(ctx, res.calls, outcome) if res.route is Route.TOOLS else res.text
        except Exception as e:  # never let an orchestration bug take the turn down
            logger.exception(f"[assistant] turn failed: {e}")
            return AgentResult(agent_name=self.name, response=_ERROR_REPLY)

        reply = self._backstop(reply, outcome)
        self._persist(ctx, reply)
        self._write_state(ctx, outcome)
        self._record_trace(ctx, res.route, result.decision, outcome, reply, started, state, active)
        return AgentResult(agent_name=self.name, response=reply, skill_calls=list(ctx.skill_results))

    async def execute_stream(self, ctx: AgentContext, cancel_event: asyncio.Event | None = None) -> AsyncIterator[str]:
        self._begin(ctx)
        started = time.perf_counter()
        try:
            result, res, state, active, outcome = await self._decide(ctx)
            if res.route is Route.CHAT:
                async for chunk in self._responder.execute_stream(ctx, cancel_event=cancel_event):
                    yield chunk
                self._record_trace(ctx, res.route, result.decision, outcome, "", started, state, active)
                return
            reply = await self._run_tools(ctx, res.calls, outcome) if res.route is Route.TOOLS else res.text
        except Exception as e:
            logger.exception(f"[assistant] turn failed: {e}")
            yield _ERROR_REPLY
            return

        # A tool, clarification, refusal or failure has no model text to stream: the reply exists only now.
        reply = self._backstop(reply, outcome)
        self._persist(ctx, reply)
        self._write_state(ctx, outcome)
        self._record_trace(ctx, res.route, result.decision, outcome, reply, started, state, active)
        yield reply
