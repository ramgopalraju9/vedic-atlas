"""SkillRegistry — discovers, registers, and manages skills.

Donor: veda/skills/registry.py, read in full and ported near-verbatim,
only the exception/logger import paths changed.
"""

from core.enums import ErrorMessage, ExceptionCode
from exceptions.exception import AppException
from core.logging_config import logger
from service.skills.base_skill import BaseSkill


class SkillRegistry:
    """Central registry for all skills available to agents."""

    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        if skill.name in self._skills:
            logger.warning(f"Skill '{skill.name}' already registered, overwriting")
        self._skills[skill.name] = skill
        logger.info(f"Registered skill: {skill.name} (permission: {skill.permission_level})")

    def get(self, name: str) -> BaseSkill:
        if name not in self._skills:
            raise AppException(
                class_name="SkillRegistry",
                code=ExceptionCode.SKILL_NOT_FOUND,
                error_message=ErrorMessage.SKILL_NOT_REGISTERED,
                skill_name=name,
            )
        return self._skills[name]

    def list_all(self) -> list[BaseSkill]:
        return list(self._skills.values())

    def list_enabled(self) -> list[BaseSkill]:
        return [s for s in self._skills.values() if s.enabled]

    def list_by_names(self, names: list[str]) -> list[BaseSkill]:
        return [self._skills[n] for n in names if n in self._skills and self._skills[n].enabled]

    def get_prompt_descriptions(self, skill_names: list[str] | None = None) -> str:
        """Generate the tools prompt block for the model."""
        skills = self.list_by_names(skill_names) if skill_names is not None else self.list_enabled()
        if not skills:
            return ""
        return "\n".join(s.to_prompt_block() for s in skills)