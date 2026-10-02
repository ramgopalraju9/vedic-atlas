"""SkillDefinition — the declarative half of a skill.

Split out of veda/meta/skill.py's BaseSkill ABC. BaseSkill mixed a pure
descriptor (name, description, permission level, input schema) with
executable behaviour (the abstract `execute` coroutine, the prompt-block
renderer). Only the descriptor belongs in the domain layer — it has no
I/O and nothing to execute. The runnable half (BaseSkill, the `execute`
contract, the @skill decorator) is staged at service/skills/base_skill.py.

Renamed `to_anthropic_tool` is dropped entirely — that was Claude-CLI tool
schema shaping. Skill invocation in the target architecture goes through
SkillService, not model-vendor tool-call plumbing.
"""

from dataclasses import dataclass, field
from typing import Any

from domain.value_objects.permission_level import PermissionLevel


@dataclass
class SkillDefinition:
    """Pure descriptor for a registered skill — no execution behaviour."""

    name: str
    description: str
    permission_level: PermissionLevel = PermissionLevel.APPROVE
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "additionalProperties": True})
    enabled: bool = True

    def to_prompt_block(self) -> str:
        """Render this skill for injection into the model's prompt."""
        return f"- {self.name}: {self.description}"