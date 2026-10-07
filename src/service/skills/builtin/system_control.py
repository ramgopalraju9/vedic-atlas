"""Device-control tools: app_control, volume_control, device_status (replace SystemAgent's planner).

SystemAgent was a third decision point: its own model call with no JSON schema, a hardcoded action catalogue,
no history, and a dispatch straight to SystemControlPort that bypassed SkillRunner. These skills are the same
actions as ordinary manifest tools, so the one control decode picks them under a grammar and every call goes
through SkillRunner (permission, rate limit, validation, audit).

The spoken sentences are built from the port's real return values, as SystemAgent did, so a template reply can
never claim something the device did not confirm. Port calls block (psutil / OS APIs), so they run in a thread.
The `system_action` governance check SystemAgent made is kept here, unchanged in meaning.
"""

from __future__ import annotations

import asyncio

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from domain.entities.tool_manifest import ToolManifest
from domain.ports.governance_port import GovernanceProvider
from domain.ports.system_control_port import SystemControlPort
from service.skills.manifest_skill import ManifestSkill

_VOLUME_MIN, _VOLUME_MAX = 0, 100
_PROCESS_LIMIT = 5


class _SystemSkill(ManifestSkill):
    what = "do that on the device"

    def __init__(
        self, system_control: SystemControlPort, manifest: ToolManifest,
        governance: GovernanceProvider | None = None, **kw,
    ):
        super().__init__(manifest, **kw)
        self._sys = system_control
        self._governance = governance

    def _denied(self, action: str, target: str) -> SkillResult | None:
        if self._governance is None:
            return None
        decision = self._governance.check_action("system_action", {"tool": action, "target": target})
        if decision.allowed:
            return None
        return self._fail(decision.reason, f"I can't do that - governance policy: {decision.reason}")

    @staticmethod
    async def _off_loop(fn, *args):
        return await asyncio.to_thread(fn, *args)


class AppControlSkill(_SystemSkill):
    """Open, close or focus an application."""

    what = "control that app"
    _METHODS = {"open": "open_app", "close": "close_app", "focus": "focus_app"}

    async def run(self, ctx: AgentContext, action: str = "", name: str = "", **_) -> SkillResult:
        method = self._METHODS.get(action)
        if method is None:
            return self._fail(f"unknown action '{action}'", "I'm not sure what to do with that app.")
        if not (name or "").strip():
            return self._fail("no app name", "Which app do you mean?")
        denied = self._denied(f"{action}_app", name)
        if denied is not None:
            return denied
        ok, message = await self._off_loop(getattr(self._sys, method), name.strip())
        return self._ok(message, message) if ok else self._fail(message, message)


class VolumeControlSkill(_SystemSkill):
    """Read or change the volume; mute or unmute."""

    what = "change the volume"

    async def run(self, ctx: AgentContext, action: str = "", level: int | None = None, **_) -> SkillResult:
        denied = self._denied(f"{action}_volume", str(level) if level is not None else "")
        if denied is not None:
            return denied

        if action == "get":
            volume = await self._off_loop(self._sys.get_volume)
            if volume is None:
                return self._fail("volume unavailable", "I couldn't read the volume right now.")
            muted = await self._off_loop(self._sys.is_muted)
            text = f"Volume is at {volume}%{' (muted)' if muted else ''}."
            return self._ok(text, text)

        if action == "set":
            if level is None or not (_VOLUME_MIN <= int(level) <= _VOLUME_MAX):   # range enforced in code, not by the model
                return self._fail(f"level {level!r} out of range", f"The volume has to be between {_VOLUME_MIN} and {_VOLUME_MAX}.")
            ok, message = await self._off_loop(self._sys.set_volume, int(level))
            return self._ok(message, message) if ok else self._fail(message, message)

        if action in ("mute", "unmute"):
            on = action == "mute"
            ok = await self._off_loop(self._sys.mute, on)
            if not ok:
                return self._fail("mute failed", "I couldn't change the mute state.")
            text = "Muted." if on else "Unmuted."
            return self._ok(text, text)

        return self._fail(f"unknown action '{action}'", "I'm not sure what to do with the volume.")


class DeviceStatusSkill(_SystemSkill):
    """Battery level, or the busiest running applications."""

    what = "read the device status"

    async def run(self, ctx: AgentContext, what: str = "", **_) -> SkillResult:
        denied = self._denied(what or "status", "")
        if denied is not None:
            return denied

        if what == "battery":
            info = await self._off_loop(self._sys.battery_info)
            if not info.get("has_battery"):
                text = "This device doesn't report a battery."
                return self._ok(text, text)
            state = "charging" if info.get("plugged_in") else "on battery"
            secs = info.get("secs_left")
            tail = f", about {int(secs / 60)} minutes left" if secs and secs > 0 and not info.get("plugged_in") else ""
            text = f"Battery is at {info['percent']}%, {state}{tail}."
            return self._ok(text, text)

        if what == "processes":
            procs = await self._off_loop(self._sys.top_processes, _PROCESS_LIMIT)
            if not procs:
                text = "Nothing notable running."
                return self._ok(text, text)
            text = "Busiest right now: " + ", ".join(f"{p['name']} ({p['cpu']}%)" for p in procs[:_PROCESS_LIMIT]) + "."
            return self._ok(text, text)

        return self._fail(f"unknown status '{what}'", "I can only report the battery or what's running.")
