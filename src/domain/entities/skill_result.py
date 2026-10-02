from dataclasses import dataclass, field
from typing import Any

@dataclass
class SkillResult:
    """The output of a skill execution."""
    skill_name: str
    success: bool
    output: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
