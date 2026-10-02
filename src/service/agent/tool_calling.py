"""ToolCallingLoop — lets an agent call skills via a JSON action protocol.

New (Feature C). Opt-in. Every skill call goes through SkillRunner, so the
existing permission/approval/rate-limit/validator/audit hooks apply; a
blocked or failed call is fed back to the model as an observation rather
than bypassing a guardrail. A hard iteration cap bounds latency.
"""

from __future__ import annotations

import json
import re

from core.logging_config import logger
from domain.entities.agent_context import AgentContext
from domain.value_objects.tool_call import ToolCall
from service.skills.registry import SkillRegistry
from service.skills.skill_runner import SkillRunner

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_TOOL_INSTRUCTIONS = """You can use tools to answer. Available tools:
{tools}

To call a tool, reply with EXACTLY one line of JSON, no markdown:
  {{"tool": "<name>", "args": {{...}}}}
When you have the answer, reply with EXACTLY:
  {{"final": "<your reply to the user>"}}
Call a tool only when it is actually needed; otherwise reply with final immediately."""


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
        max_iterations: int = 3,
    ):
        self._client = client
        self._skill_runner = skill_runner
        self._skill_registry = skill_registry
        self._tool_names = tool_names
        self._model = model
        self._max_iterations = max_iterations

    async def run(self, ctx: AgentContext, system: str, user: str) -> str:
        tools_block = self._skill_registry.get_prompt_descriptions(self._tool_names)
        system_prompt = f"{system}\n\n{_TOOL_INSTRUCTIONS.format(tools=tools_block)}"
        transcript = user
        for _ in range(self._max_iterations):
            try:
                raw = await self._client.complete(prompt=transcript, system=system_prompt, model=self._model)
            except Exception as e:
                logger.error(f"[tool-loop] inference failed: {e}")
                return ""
            parsed = self._parse(raw)
            if parsed is None:
                return raw.strip()  # plain text = final answer
            if "final" in parsed:
                return str(parsed.get("final") or "").strip()
            name = parsed.get("tool")
            if not name:
                return raw.strip()
            args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
            observation = await self._invoke(ctx, ToolCall(name=str(name), args=args))
            transcript += (
                f"\n\nYou called: {json.dumps({'tool': name, 'args': args})}\n"
                f"OBSERVATION: {observation}\n\n"
                'Continue: call another tool, or reply with {"final": "..."}.'
            )
        logger.info("[tool-loop] max iterations reached")
        return "I couldn't finish that within my tool budget — could you narrow it down?"

    async def _invoke(self, ctx: AgentContext, call: ToolCall) -> str:
        if call.name not in self._tool_names:
            return f"ERROR: tool '{call.name}' is not available."
        try:
            result = await self._skill_runner.execute_skill(ctx, call.name, **call.args)
        except Exception as e:
            return f"ERROR: {e}"
        if result.success:
            return str(result.output)[:800] if result.output is not None else "ok"
        return f"ERROR: {result.error or 'blocked'}"

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