"""SkillRunner — skill execution with hook-based guardrail enforcement.

Donor: veda/service/skill_service.py's SkillService, read in full and
ported near-verbatim. Confirms a finding from earlier grounding: this
class DOES correctly implement hook firing when given a HookRegistry —
the gap in the donor was never here, it was that `app.py::_bootstrap_agents`
constructed this with `hook_registry=None`. server.py's composition step
(a later batch) must pass a real HookRegistry for pre/post-skill hooks
to actually fire.
"""

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from core.enums import ErrorMessage, ExceptionCode, HookEvent
from exceptions.exception import AppException
from core.logging_config import logger
from service.hooks.registry import HookRegistry
from service.skills.registry import SkillRegistry


class SkillRunner:
    """Orchestrates skill execution with hook-based guardrails.

    Routes and agents call this service instead of skills directly.
    Pre-skill hooks enforce permissions, rate limits, and input validation.
    Post-skill hooks handle output validation and audit logging.
    """

    def __init__(self, skill_registry: SkillRegistry, hook_registry: HookRegistry | None = None):
        self.skill_registry = skill_registry
        self.hook_registry = hook_registry

    async def execute_skill(self, ctx: AgentContext, skill_name: str, **params) -> SkillResult:
        skill = self.skill_registry.get(skill_name)

        if not skill.enabled:
            raise AppException(
                class_name="SkillRunner",
                code=ExceptionCode.SKILL_NOT_FOUND,
                error_message=ErrorMessage.SKILL_DISABLED,
                skill_name=skill_name,
            )

        if self.hook_registry:
            pre_results = await self.hook_registry.fire(
                HookEvent.PRE_SKILL,
                ctx=ctx,
                skill_name=skill_name,
                permission_level=skill.permission_level,
                params=params,
            )
            if False in pre_results:
                logger.warning(f"Pre-skill hook aborted execution of '{skill_name}'")
                return SkillResult(skill_name=skill_name, success=False, error="Blocked by guardrails")

        logger.info(f"Executing skill '{skill_name}' (permission: {skill.permission_level}, agent: {ctx.current_agent})")
        result = await skill.execute(ctx, **params)
        # A private skill's output (mail, calendar) must not reach the audit log or the API's skill_calls.
        shown = "" if getattr(skill, "sensitive_output", False) else (str(result.output)[:500] if result.output else "")

        if self.hook_registry:
            await self.hook_registry.fire(
                HookEvent.POST_SKILL,
                ctx=ctx,
                skill_name=skill_name,
                params=params,
                success=result.success,
                output=shown,
                error=result.error or "",
            )

        ctx.skill_results.append({
            "skill": skill_name,
            "success": result.success,
            "output": shown or None,
            "error": result.error,
        })

        if result.success:
            logger.info(f"Skill '{skill_name}' completed successfully")
        else:
            logger.warning(f"Skill '{skill_name}' failed: {result.error}")

        return result