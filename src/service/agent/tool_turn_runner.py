"""ToolTurnRunner — one tool-using turn, in small, checkable stages.

    A. DECIDE   a tiny prompt (persona-lite + this agent's tools + last 2 turns) and a
                JSON-schema-constrained completion -> {"calls": [...]}. The model can't
                emit malformed JSON, an unknown tool or a bad argument.
       FORCE    if the user's message matches a tool's `required_when` (e.g. "my tasks",
                "weather") and the model returned no call, retry once with minItems=1.
    B. EXECUTE  each call goes through SkillRunner (permission / rate-limit / validators /
                audit hooks all still apply). Every call is logged.
    C. REPLY    template tools: the reply is the tool's own `spoken` text (nothing for the
                model to embellish, no second inference). LLM tools: a short narrate-stage
                completion over the capped tool result, then an optional grounding check.

Returns `reply=None` when the model decided no tool is needed, so the owning agent can
hand the turn to plain chat.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Sequence

from core.logging_config import logger
from domain.entities.agent_context import AgentContext
from domain.entities.tool_manifest import REPLY_TEMPLATE, ToolManifest
from domain.policies.grounding_policy import is_grounded
from domain.policies.tool_call_schema import MAX_CALLS, build_call_schema
from domain.ports.inference_port import InferencePort
from service.agent.tool_use_guard import ToolUseGuard
from service.prompting.prompt_composer import PromptComposer
from service.skills.skill_runner import SkillRunner

_FORCE_NOTE = "\nNOTE: this message is about {tools}. You must call a tool - do not reply with an empty list."
_FAIL_REPLY = "I couldn't do that: {error}"


@dataclass
class ExecutedCall:
    tool: str
    args: dict[str, Any]
    ok: bool
    observation: str   # compact text the narrate stage may use
    spoken: str | None  # the tool's own user-facing sentence (template mode)
    final: bool         # the tool says `spoken` is ready as-is (skip narration)
    error: str | None
    ms: int


@dataclass
class TurnOutcome:
    reply: str | None  # None => no tool needed, hand to plain chat
    calls: list[ExecutedCall] = field(default_factory=list)
    forced: bool = False
    narrated: bool = False
    prompt_tokens: dict[str, int] = field(default_factory=dict)
    timings_ms: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


async def execute_call(skill_runner: SkillRunner, ctx: AgentContext, call: dict[str, Any]) -> ExecutedCall:
    """Run one call through SkillRunner (permission / rate-limit / validators / audit hooks all apply) and log it."""
    tool, args = call["tool"], call["args"]
    started = time.perf_counter()
    try:
        result = await skill_runner.execute_skill(ctx, tool, **args)
        ok = bool(result.success)
        observation = str(result.output) if ok and result.output is not None else ""
        error = None if ok else (result.error or "blocked")
        spoken = (result.metadata or {}).get("spoken")
        final = bool((result.metadata or {}).get("final"))
    except Exception as e:
        ok, observation, error, spoken, final = False, "", str(e), None, False
    ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        f"[tool-call] agent={ctx.current_agent} tool={tool} args={json.dumps(args, default=str)} "
        f"ok={ok} ms={ms} result={(observation or error or '')[:200]!r}"
    )
    return ExecutedCall(tool, args, ok, observation, spoken, final, error, ms)


async def narrate_with_grounding(
    *,
    client: InferencePort,
    composer: PromptComposer,
    grounding_check: Callable[[str, Sequence[str], str], bool],
    ctx: AgentContext,
    results: Sequence[str],
    model: str | None,
    num_predict: int,
    temperature: float,
    outcome: "TurnOutcome",
) -> str | None:
    """The narrate/content stage: one short completion over capped tool results, re-checked for ungrounded numbers
    (two attempts). Returns the text, or None when the model failed or kept inventing numbers; the caller then
    falls back to the deterministic reply."""
    stage_b = composer.narrate_stage(ctx.user_message, results)
    outcome.prompt_tokens["narrate"] = stage_b.tokens
    for attempt in range(2):
        try:
            text = (await client.complete(
                prompt=stage_b.prompt, system=stage_b.system, model=model,
                temperature=temperature, num_predict=num_predict,
            )).strip()
        except Exception as e:
            logger.error(f"[tool-turn] narrate stage inference failed: {e}")
            return None
        if not text:
            continue
        if grounding_check(text, results, ctx.user_message):
            outcome.narrated = True
            return text
        logger.warning(f"[tool-guard] ungrounded narration (attempt {attempt + 1}): {text[:160]!r}")
        outcome.notes.append("ungrounded narration rejected")
    return None


class ToolTurnRunner:
    def __init__(
        self,
        *,
        client: InferencePort,
        composer: PromptComposer,
        skill_runner: SkillRunner,
        manifests: dict[str, ToolManifest],
        guard: ToolUseGuard,
        conversation=None,
        model: str | None = None,
        call_num_predict: int = 160,
        narrate_num_predict: int = 90,
        call_temperature: float = 0.1,
        narrate_temperature: float = 0.2,
        grounding_check: Callable[[str, Sequence[str], str], bool] | None = None,
    ):
        self._client = client
        self._composer = composer
        self._skill_runner = skill_runner
        self._manifests = manifests
        self._guard = guard
        self._conversation = conversation
        self._model = model
        self._call_num_predict = call_num_predict
        self._narrate_num_predict = narrate_num_predict
        self._call_temperature = call_temperature
        self._narrate_temperature = narrate_temperature
        self._grounding_check = grounding_check or self._default_grounding

    @staticmethod
    def _default_grounding(reply: str, results: Sequence[str], user_message: str) -> bool:
        """Numbers in a narration must come from the tool result, the question, or today's date."""
        today = datetime.now().strftime("%A %d %B %Y %H:%M")
        return is_grounded(reply, [*results, user_message, today])

    # ---- public -----------------------------------------------------------

    async def run(self, ctx: AgentContext, tool_names: Sequence[str]) -> TurnOutcome:
        outcome = TurnOutcome(reply=None)
        manifests = self._composer.manifests_for(tool_names)
        if not manifests:
            return outcome
        history = self._conversation.turns if self._conversation is not None else []
        stage_a = self._composer.call_stage(tool_names, ctx.user_message, history)
        outcome.prompt_tokens["call"] = stage_a.tokens
        if stage_a.trimmed:
            outcome.notes.append(f"call prompt trimmed: {stage_a.trimmed}")

        t0 = time.perf_counter()
        calls = await self._decide(stage_a.system, stage_a.prompt, manifests, min_calls=0)
        outcome.timings_ms["decide"] = int((time.perf_counter() - t0) * 1000)

        required = [t for t in self._guard.required_tools(ctx.user_message) if t in tool_names]
        if not calls and required:
            outcome.forced = True
            logger.warning(f"[tool-guard] no call for required tool(s) {required}; forcing. user={ctx.user_message!r}")
            t1 = time.perf_counter()
            calls = await self._decide(
                stage_a.system,
                stage_a.prompt + _FORCE_NOTE.format(tools=", ".join(required)),
                self._composer.manifests_for(required),
                min_calls=1,
            )
            outcome.timings_ms["force"] = int((time.perf_counter() - t1) * 1000)

        if not calls:
            logger.info(f"[tool-turn] agent={ctx.current_agent} decided=no-tool user={ctx.user_message!r}")
            return outcome

        t2 = time.perf_counter()
        for call in calls[:MAX_CALLS]:
            outcome.calls.append(await self._execute(ctx, call))
        outcome.timings_ms["execute"] = int((time.perf_counter() - t2) * 1000)

        t3 = time.perf_counter()
        outcome.reply = await self._reply(ctx, outcome)
        outcome.timings_ms["reply"] = int((time.perf_counter() - t3) * 1000)
        logger.info(
            f"[tool-turn] agent={ctx.current_agent} calls={[c.tool for c in outcome.calls]} "
            f"ok={[c.ok for c in outcome.calls]} forced={outcome.forced} narrated={outcome.narrated} "
            f"timings_ms={outcome.timings_ms} tokens={outcome.prompt_tokens}"
        )
        return outcome

    # ---- stage A ----------------------------------------------------------

    async def _decide(
        self, system: str, prompt: str, manifests: list[ToolManifest], *, min_calls: int
    ) -> list[dict[str, Any]]:
        schema = build_call_schema(manifests, min_calls=min_calls)
        try:
            raw = await self._client.complete(
                prompt=prompt, system=system, model=self._model, json_schema=schema,
                temperature=self._call_temperature, num_predict=self._call_num_predict,
            )
        except Exception as e:
            logger.error(f"[tool-turn] decide stage inference failed: {e}")
            return []
        return self._parse_calls(raw, {m.name for m in manifests})

    @staticmethod
    def _parse_calls(raw: str, allowed: set[str]) -> list[dict[str, Any]]:
        try:
            data = json.loads(raw)
            items = data["calls"]
        except (TypeError, ValueError, KeyError):
            logger.warning(f"[tool-turn] unparseable decide output: {str(raw)[:160]!r}")
            return []
        calls = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get("tool") in allowed and isinstance(item.get("args"), dict):
                calls.append({"tool": item["tool"], "args": item["args"]})
        return calls

    # ---- stage B ----------------------------------------------------------

    async def _execute(self, ctx: AgentContext, call: dict[str, Any]) -> ExecutedCall:
        return await execute_call(self._skill_runner, ctx, call)

    # ---- stage C ----------------------------------------------------------

    @staticmethod
    def _deterministic_reply(calls: list[ExecutedCall]) -> str:
        parts = []
        for c in calls:
            if c.spoken:
                parts.append(c.spoken)
            elif c.ok:
                parts.append(c.observation.splitlines()[0] if c.observation else "Done.")
            else:
                parts.append(_FAIL_REPLY.format(error=c.error))
        return " ".join(parts)

    async def _reply(self, ctx: AgentContext, outcome: TurnOutcome) -> str:
        calls = outcome.calls
        deterministic = self._deterministic_reply(calls)
        if all(self._manifests[c.tool].reply_mode == REPLY_TEMPLATE or (c.ok and c.final) for c in calls):
            return deterministic
        if not any(c.ok for c in calls):
            return deterministic  # nothing to narrate; say plainly what failed

        results = [c.observation if c.ok else f"ERROR: {c.error}" for c in calls]
        text = await narrate_with_grounding(
            client=self._client, composer=self._composer, grounding_check=self._grounding_check, ctx=ctx,
            results=results, model=self._model, num_predict=self._narrate_num_predict,
            temperature=self._narrate_temperature, outcome=outcome,
        )
        return text if text is not None else deterministic
