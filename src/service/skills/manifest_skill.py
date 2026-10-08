"""ManifestSkill — BaseSkill whose identity comes from a ToolManifest.

Name, description, permission level and argument schema are read from the
manifest (config/tools/<name>.yaml), so a skill class only holds behaviour
(`run`). This base also gives every tool the same safety envelope:

  - unknown arguments are rejected before `run` is called,
  - provider/validation errors become a clean failed SkillResult with a plain
    `spoken` sentence (never a stack trace, never an invented answer),
  - every result carries `spoken` in metadata — the user-facing reply for
    template-mode tools and the fallback when a narration is rejected.
"""

from __future__ import annotations

from abc import abstractmethod

from core.enums import ExceptionCode
from core.logging_config import logger
from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from domain.entities.tool_manifest import ToolManifest
from domain.entities.tool_observation import ToolObservation
from domain.policies.tool_call_schema import tool_args_schema
from exceptions.exception import AppException, ToolUnavailableError
from service.skills.base_skill import BaseSkill


class ManifestSkill(BaseSkill):
    # Used in the spoken failure line: "I couldn't <what> right now."
    what = "do that"

    def __init__(self, manifest: ToolManifest, permission_level: str | None = None, enabled: bool = True):
        super().__init__(
            name=manifest.name,
            description=manifest.description,
            permission_level=permission_level or manifest.permission_level,
            enabled=enabled,
        )
        self._manifest = manifest
        self.sensitive_output = manifest.private   # SkillRunner keeps this skill's output out of hooks and skill_results
        self._allowed = frozenset(p.name for p in manifest.params)

    def get_parameters_description(self) -> str:
        return ", ".join(f"{p.name}{'' if p.required else '?'}" for p in self._manifest.params)

    def get_input_schema(self) -> dict:
        return tool_args_schema(self._manifest)

    # ---- result helpers -----------------------------------------------------

    def _fail(self, error: str, spoken: str | None = None) -> SkillResult:
        return SkillResult(
            skill_name=self.name, success=False, error=error,
            metadata={"spoken": spoken or f"I couldn't do that: {error}"},
        )

    def _ok(self, output: str, spoken: str, **metadata) -> SkillResult:
        return SkillResult(
            skill_name=self.name, success=True, output=output, metadata={"spoken": spoken, **metadata}
        )

    def _from_observation(self, obs: ToolObservation) -> SkillResult:
        return self._ok(obs.text, obs.spoken, source=obs.source, as_of=obs.as_of, cached=obs.cached, final=obs.final)

    def _from_error(self, e: AppException) -> SkillResult:
        if isinstance(e, ToolUnavailableError):
            if e.not_configured:
                spoken = f"I can't {self.what} yet because it isn't set up."
            else:
                spoken = f"I couldn't {self.what} right now. The service didn't respond."
            return self._fail(e.message, spoken)
        if e.code in (ExceptionCode.NOT_FOUND, ExceptionCode.VALIDATION_ERROR):
            return self._fail(e.message, f"I couldn't {self.what}: {e.message.rstrip('.')}.")
        return self._fail(e.message, f"I couldn't {self.what} right now.")

    # ---- execution ----------------------------------------------------------

    async def execute(self, ctx: AgentContext, **params) -> SkillResult:
        unknown = set(params) - self._allowed
        if unknown:
            return self._fail(f"Unknown parameter(s): {', '.join(sorted(unknown))}")
        try:
            return await self.run(ctx, **params)
        except AppException as e:
            logger.warning(f"[{self.name}] failed: {e.code} {e.message}")
            return self._from_error(e)
        except Exception as e:
            logger.exception(f"[{self.name}] unexpected failure")
            return self._fail(str(e), f"I couldn't {self.what} right now.")

    @abstractmethod
    async def run(self, ctx: AgentContext, **params) -> SkillResult:
        """The tool's behaviour. May raise AppException for expected failures."""
