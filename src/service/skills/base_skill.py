"""BaseSkill — abstract base + @skill decorator for function-based skills.

Donor: veda/meta/skill.py, read in full during Batch 2 grounding
(SkillResult, the sibling entity in this donor file, already landed at
domain/entities/skill_result.py). Ported verbatim except:
  - `to_anthropic_tool()` dropped — Claude-CLI tool-schema shaping, not
    needed once skill invocation goes through SkillService rather than
    vendor tool-call plumbing.
  - Uses SkillDefinition (domain/entities, Batch 4) for the declarative
    shape rather than duplicating name/description/permission_level as
    loose constructor args.

Correction (2026-09-22): the `skill()` decorator originally read
`func.__doc__` for `params_description` — not what the donor does. The
real donor introspects `inspect.signature(func)`, skipping the `ctx`
param and noting required-vs-optional-with-default for each remaining
parameter. Fixed to match.
"""

import inspect
from abc import ABC, abstractmethod
from typing import Any, Callable

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from domain.value_objects.permission_level import PermissionLevel


class BaseSkill(ABC):
    """Abstract base class for all skills.

    Skills are tools agents can invoke — terminal commands, file ops, web
    lookups, etc. Each skill has a permission level determining whether
    execution is auto, notify, or requires approval.
    """

    def __init__(
        self,
        name: str,
        description: str,
        permission_level: str = "approve",
        enabled: bool = True,
    ):
        self.name = name
        self.description = description
        self.permission_level = PermissionLevel(permission_level)
        self.enabled = enabled

    @abstractmethod
    def get_parameters_description(self) -> str:
        """Human-readable parameter description for prompt injection."""

    def get_input_schema(self) -> dict[str, Any]:
        """JSONSchema describing this skill's parameters. Override for precise typing."""
        return {"type": "object", "additionalProperties": True}

    @abstractmethod
    async def execute(self, ctx: AgentContext, **params) -> SkillResult:
        """Execute the skill with the given parameters."""

    def to_prompt_block(self) -> str:
        """Text block describing this skill for the model's prompt."""
        return f"- {self.name}: {self.description}\n  {self.get_parameters_description()}"


class _FunctionSkill(BaseSkill):
    """Concrete skill wrapping a plain async function, created by @skill."""

    def __init__(
        self,
        name: str,
        description: str,
        permission_level: str,
        func: Callable,
        params_description: str,
        input_schema: dict[str, Any] | None = None,
    ):
        super().__init__(name, description, permission_level)
        self._func = func
        self._params_description = params_description
        self._input_schema = input_schema

    def get_parameters_description(self) -> str:
        return self._params_description

    def get_input_schema(self) -> dict[str, Any]:
        return self._input_schema if self._input_schema is not None else super().get_input_schema()

    async def execute(self, ctx: AgentContext, **params) -> SkillResult:
        try:
            result = await self._func(ctx=ctx, **params)
            if isinstance(result, SkillResult):
                return result
            return SkillResult(skill_name=self.name, success=True, output=result)
        except Exception as e:
            return SkillResult(skill_name=self.name, success=False, error=str(e))


def skill(
    name: str,
    description: str,
    permission_level: str = "approve",
    input_schema: dict[str, Any] | None = None,
) -> Callable:
    """Decorator that converts an async function into a BaseSkill."""

    def decorator(func: Callable) -> _FunctionSkill:
        sig = inspect.signature(func)
        parts: list[str] = []
        for pname, param in sig.parameters.items():
            if pname == "ctx":
                continue
            if param.default is inspect.Parameter.empty:
                parts.append(f"{pname} (required)")
            else:
                parts.append(f"{pname} (optional, default: {param.default})")
        params_description = f"Parameters: {', '.join(parts)}" if parts else "No parameters"
        return _FunctionSkill(
            name=name,
            description=description,
            permission_level=permission_level,
            func=func,
            params_description=params_description,
            input_schema=input_schema,
        )

    return decorator