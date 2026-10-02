from __future__ import annotations

import asyncio
import json
import re
from typing import AsyncIterator

from domain.entities.agent_context import AgentContext
from domain.entities.agent_result import AgentResult
from domain.ports.governance_port import GovernanceProvider
from domain.ports.inference_port import InferencePort
from domain.ports.system_control_port import SystemControlPort
from service.agent.base_llm_agent import LLMAgent
from core.logging_config import logger

SYSTEM_PROMPT = """You are Veda's system-control planner. The user wants something done on their device.
Pick exactly ONE action and respond with EXACTLY one line of JSON, no markdown.

Shape:
  {"action": "<name>", "args": {...}, "reason": "<one short sentence of what you're doing>"}

Available actions (use these action names verbatim):
  - open_app        args: {"name": "<app>"}     - launch the app (or focus if already open)
  - close_app       args: {"name": "<app>"}     - close the app
  - focus_app       args: {"name": "<app>"}     - switch to the app if running
  - battery_info    args: {}                    - report battery level + charging state
  - get_volume      args: {}                    - report current volume
  - set_volume      args: {"level": 0..100}     - set volume
  - mute            args: {"on": true|false}    - mute or unmute
  - top_processes   args: {}                    - list the busiest apps right now
  - none            args: {}                    - if it isn't a system-control ask, pick this

Examples:
  "open VS Code"               -> {"action":"open_app","args":{"name":"vs code"},"reason":"Opening VS Code."}
  "please close spotify"       -> {"action":"close_app","args":{"name":"spotify"},"reason":"Closing Spotify."}
  "bring chrome to front"      -> {"action":"focus_app","args":{"name":"chrome"},"reason":"Switching to Chrome."}
  "battery?"                   -> {"action":"battery_info","args":{},"reason":"Checking battery."}
  "turn volume to 30"          -> {"action":"set_volume","args":{"level":30},"reason":"Setting volume to 30."}
  "mute"                       -> {"action":"mute","args":{"on":true},"reason":"Muting audio."}
  "what's running"             -> {"action":"top_processes","args":{},"reason":"Checking busiest apps."}
  "tell me a joke"             -> {"action":"none","args":{},"reason":"Not a system action."}
"""

_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)

# A single-line JSON action is short; cap generation so a stray ramble can't
# run to the full default budget. See docs/PERF_BRIEF.md §5.1.
_PLANNER_NUM_PREDICT = 96


class SystemAgent(LLMAgent):
    """Specialist for device-level actions."""

    def __init__(
        self,
        client: InferencePort,
        system_control: SystemControlPort,
        model: str | None = None,
        governance: GovernanceProvider | None = None,
        **kwargs,
    ):
        super().__init__(
            name="system",
            description=(
                "Device actions: opening apps, volume control, listing running apps. "
                'Use for "open VS Code", "set volume to 30", "what\'s running".'
            ),
            system_prompt=SYSTEM_PROMPT,
            client=client,
            model=model,
            **kwargs,
        )
        self.system_control = system_control
        self.governance = governance

    async def execute(self, ctx: AgentContext) -> AgentResult:
        self._mark_active(ctx)
        try:
            raw = await self.client.complete(
                prompt=ctx.user_message,
                system=SYSTEM_PROMPT,
                model=self.model,
                num_predict=_PLANNER_NUM_PREDICT,
            )
        except Exception as e:
            logger.error(f"[system-agent] planner call failed: {e}")
            reply = "Something went wrong planning that action - try again?"
            await self._on_completion(ctx, reply)
            return AgentResult(agent_name=self.name, response=reply)

        plan = self._parse(raw)
        if plan is None:
            logger.warning(f"[system-agent] couldn't parse: {raw[:200]!r}")
            reply = "I wasn't sure what to do there. Try again a bit more specifically?"
            await self._on_completion(ctx, reply)
            return AgentResult(agent_name=self.name, response=reply)

        action = plan.get("action", "")
        args = plan.get("args") or {}
        logger.info(f"[system-agent] action={action} args={args}")

        if self.governance is not None:
            decision = self.governance.check_action("system_action", {"tool": action, "target": str(args)})
            if not decision.allowed:
                logger.warning(f"[system-agent][governance] action '{action}' denied: {decision.reason}")
                reply = f"I can't do that - governance policy: {decision.reason}"
                await self._on_completion(ctx, reply)
                return AgentResult(agent_name=self.name, response=reply)

        reply = await asyncio.to_thread(self._dispatch, action, args)
        self._record_to_memory(action=action, context={"args": args}, user_message=ctx.user_message)
        await self._on_completion(ctx, reply)
        return AgentResult(agent_name=self.name, response=reply)

    async def execute_stream(self, ctx: AgentContext, cancel_event=None) -> AsyncIterator[str]:
        # Planner + dispatcher is fast enough that streaming adds no value.
        result = await self.execute(ctx)
        yield result.response

    def _parse(self, raw: str) -> dict | None:
        if not raw:
            return None
        text = _JSON_FENCE_RE.sub("", raw).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None

    def _dispatch(self, action: str, args: dict) -> str:
        try:
            if action == "open_app":
                _msg = self.system_control.open_app(args.get("name", ""))
                return _msg
            if action == "close_app":
                _msg = self.system_control.close_app(args.get("name", ""))
                return _msg
            if action == "focus_app":
                _msg = self.system_control.focus_app(args.get("name", ""))
                return _msg
            if action == "battery_info":
                b = self.system_control.battery_info()
                if not b.get("has_battery"):
                    return "This device doesn't report a battery."
                pct = b["percent"]
                state = "charging" if b["plugged_in"] else "on battery"
                tail = ""
                secs = b.get("secs_left")
                if secs is not None and secs > 0 and not b["plugged_in"]:
                    tail = f", about {int(secs / 60)} minutes left"
                return f"Battery is at {pct}%, {state}{tail}."
            if action == "get_volume":
                v = self.system_control.get_volume()
                if v is None:
                    return "I couldn't read the volume right now."
                muted = self.system_control.is_muted()
                return f"Volume is at {v}%{' (muted)' if muted else ''}."
            if action == "set_volume":
                _msg = self.system_control.set_volume(int(args.get("level", 50)))
                return _msg
            if action == "mute":
                ok = self.system_control.mute(bool(args.get("on", True)))
                return "Muted." if ok and args.get("on", True) else ("Unmuted." if ok else "Couldn't change mute state.")
            if action == "top_processes":
                procs = self.system_control.top_processes()
                if not procs:
                    return "Nothing notable running."
                return "Busiest right now: " + ", ".join(f"{p['name']} ({p['cpu']}%)" for p in procs)
            return "I'm not sure what to do with that."
        except Exception as e:
            logger.error(f"[system-agent] dispatch failed for action={action}: {e}")
            return "That didn't work - something went wrong on the device side."