from dataclasses import dataclass, field
from typing import Any
from domain.value_objects.permission_level import PermissionLevel

@dataclass
class SkillDefinition:
    """Pure descriptor for a registered skill – no execution behaviour."""

    name: str
    description: str
    permission_level: PermissionLevel = PermissionLevel.APPROVE
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "additionalProperties": True})
    enabled: bool = True

    def to_prompt_block(self) -> str:
        """Render this skill for injection into the model’s prompt."""
        return f"{self.name}: {self.description}"
