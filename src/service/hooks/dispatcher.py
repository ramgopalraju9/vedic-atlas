"""Built-in hooks — wire guardrails into the hook system.

Donor: veda/hooks/builtin.py, read in full and ported near-verbatim; only
import paths changed (PermissionManager/RateLimiter/AuditLogger/
InputValidator/OutputValidator now come from service.guardrails.*).
"""

from domain.entities.agent_context import AgentContext
from core.logging_config import logger
from service.guardrails.audit_log import AuditLogger
from service.guardrails.permissions import PermissionManager
from service.guardrails.rate_limit import RateLimiter
from service.guardrails.validators import InputValidator, OutputValidator


def create_permission_check_hook(permission_manager: PermissionManager):
    async def permission_check_hook(ctx: AgentContext, skill_name: str, permission_level: str, params: dict, **kw) -> bool | None:
        await permission_manager.check_permission(ctx, skill_name, permission_level, params)
        return True

    permission_check_hook.__name__ = "permission_check"
    return permission_check_hook


def create_rate_limit_hook(rate_limiter: RateLimiter):
    async def rate_limit_hook(ctx: AgentContext, skill_name: str, **kw) -> bool | None:
        rate_limiter.check(skill_name)
        return True

    rate_limit_hook.__name__ = "rate_limit_check"
    return rate_limit_hook


def create_input_validator_hook(validator: InputValidator):
    async def input_validator_hook(ctx: AgentContext, params: dict, **kw) -> bool | None:
        result = await validator.validate(ctx, content=str(params))
        if not result.passed:
            logger.warning(f"Input validation failed: {result.reason}")
            return False
        return True

    input_validator_hook.__name__ = "input_validator"
    return input_validator_hook


def create_output_validator_hook(validator: OutputValidator):
    async def output_validator_hook(ctx: AgentContext, skill_name: str, output: str = "", **kw) -> bool | None:
        if not output:
            return True
        result = await validator.validate(ctx, content=output)
        if not result.passed:
            logger.warning(f"Output validation flagged for '{skill_name}': {result.reason}")
        return True  # post-hooks warn, never abort

    output_validator_hook.__name__ = "output_validator"
    return output_validator_hook


def create_audit_hook(audit_logger: AuditLogger):
    async def audit_hook(
        ctx: AgentContext, skill_name: str = "", params: dict = None, success: bool = True,
        output: str = "", error: str = "", agent_name: str = "", event_type: str = "", **kw,
    ) -> bool | None:
        if skill_name:
            audit_logger.log_skill_execution(ctx, skill_name, params or {}, success, output, error)
        elif agent_name:
            audit_logger.log_agent_event(ctx, agent_name, event_type)
        return True

    audit_hook.__name__ = "audit_log"
    return audit_hook


def create_error_log_hook():
    async def error_log_hook(ctx: AgentContext, error: str = "", skill_name: str = "", **kw) -> bool | None:
        logger.error(f"Error in request {ctx.request_id}: skill={skill_name}, error={error}")
        return True

    error_log_hook.__name__ = "error_log"
    return error_log_hook