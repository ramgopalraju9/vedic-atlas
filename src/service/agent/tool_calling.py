"""ToolCallingLoop — lets an agent call skills via a JSON action protocol.

New (Feature C). Opt-in. Every skill call goes through SkillRunner, so the
existing permission/approval/rate-limit/validator/audit hooks apply; a
blocked or failed call is fed back to the model as an observation rather
than bypassing a guardrail. A hard iteration cap bounds latency.
"""

from __future__ import annotations

import json
import re
import time

from core.logging_config import logger
from domain.entities.agent_context import AgentContext
from domain.value_objects.tool_call import ToolCall
from service.agent.tool_use_guard import CLAIM, ToolUseGuard
from service.skills.registry import SkillRegistry
from service.skills.skill_runner import SkillRunner

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_TOOL_INSTRUCTIONS = """You can use tools to answer. Available tools:
{tools}

To call a tool, reply with EXACTLY one line of JSON, no markdown:
  {{"tool": "<name>", "args": {{...}}}}
When you have the answer, reply with EXACTLY:
  {{"final": "<your reply to the user>"}}
Rules:
- Anything about the user's tasks or to-dos (adding, listing, finishing, removing) MUST go through the tool. Never answer those from memory or from the conversation.
- Never say something was added, completed, removed or listed unless the OBSERVATION below confirms it.
- Plain conversation needs no tool: reply with final immediately.
- Never mention tools, agents, routing or the database to the user.
{examples}"""

_NUDGE = (
    "\n\nYour previous reply was: {reply}\n"
    "NOTE: that reply was rejected because no tool call was made. Do not claim anything was added, "
    "completed, removed or listed unless a tool did it. If the user is talking about their tasks, reply "
    "now with the tool call JSON. If this is just conversation, reply with final."
)

_CLAIM_FALLBACK = "Sorry, I couldn't update your tasks just now. Could you say that again?"


class ToolCallingLoop:
    """Drives a model through tool calls until it returns a final answer."""

    def __init__(
        self,
        *,
        client,
        skill_runner: SkillRunner,
        skill_registry: SkillRegistry,
        tool_names: list[str],
        model: str | None = None,
        max_iterations: int = 4,
        guard: ToolUseGuard | None = None,
    ):
        self._client = client
        self._skill_runner = skill_runner
        self._skill_registry = skill_registry
        self._tool_names = tool_names
        self._model = model
        self._max_iterations = max_iterations
        self._guard = guard

    def _examples(self) -> str:
        blocks = []
        for name in self._tool_names:
            try:
                ex = self._skill_registry.get(name).get_usage_examples()
            except Exception:
                ex = ""
            if ex:
                blocks.append(ex)
        return "\n\n".join(blocks)

    async def run(self, ctx: AgentContext, system: str, user: str) -> str:
        tools_block = self._skill_registry.get_prompt_descriptions(self._tool_names)
        instructions = _TOOL_INSTRUCTIONS.format(tools=tools_block, examples=self._examples())
        system_prompt = f"{system}\n\n{instructions}"
        transcript = user
        calls = ok_calls = 0
        nudged = False
        for _ in range(self._max_iterations):
            try:
                raw = await self._client.complete(prompt=transcript, system=system_prompt, model=self._model)
            except Exception as e:
                logger.error(f"[tool-loop] inference failed: {e}")
                return ""
            parsed = self._parse(raw)
            name = parsed.get("tool") if parsed and "final" not in parsed else None
            if not name:
                reply = str(parsed.get("final") or "").strip() if parsed and "final" in parsed else raw.strip()
                verdict = self._guard.check(ctx.user_message, reply, calls, ok_calls) if self._guard else None
                if verdict is None:
                    return reply
                logger.warning(
                    f"[tool-guard] verdict={verdict} calls={calls} ok={ok_calls} "
                    f"user={ctx.user_message!r} reply={reply[:160]!r}"
                )
                if not nudged:
                    nudged = True
                    transcript += _NUDGE.format(reply=reply[:300])
                    continue
                return _CLAIM_FALLBACK if verdict == CLAIM else reply
            args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
            ok, observation = await self._invoke(ctx, ToolCall(name=str(name), args=args))
            calls += 1
            ok_calls += 1 if ok else 0
            transcript += (
                f"\n\nYou called: {json.dumps({'tool': name, 'args': args})}\n"
                f"OBSERVATION: {observation}\n\n"
                'Continue: call another tool, or reply with {"final": "..."}.'
            )
        logger.info("[tool-loop] max iterations reached")
        return "I couldn't finish that within my tool budget — could you narrow it down?"

    async def _invoke(self, ctx: AgentContext, call: ToolCall) -> tuple[bool, str]:
        """Run one call. -> (succeeded, observation). Every call is logged."""
        started = time.perf_counter()
        if call.name not in self._tool_names:
            ok, obs = False, f"ERROR: tool '{call.name}' is not available."
        else:
            try:
                result = await self._skill_runner.execute_skill(ctx, call.name, **call.args)
                if result.success:
                    ok, obs = True, (str(result.output)[:800] if result.output is not None else "ok")
                else:
                    ok, obs = False, f"ERROR: {result.error or 'blocked'}"
            except Exception as e:
                ok, obs = False, f"ERROR: {e}"
        ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            f"[tool-call] agent={ctx.current_agent} tool={call.name} args={json.dumps(call.args, default=str)} "
            f"ok={ok} ms={ms} result={obs[:200]!r}"
        )
        return ok, obs

    @staticmethod
    def _parse(raw: str) -> dict | None:
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
        return data if isinstance(data, dict) else None