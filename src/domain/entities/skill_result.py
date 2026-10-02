"""SkillResult — the output of a skill execution.

Donor: veda/meta/skill.py (SkillResult only — the BaseSkill ABC and the
@skill decorator that lived in the same file are service-layer concerns;
they are staged at service/skills/base_skill.py in a later batch).
"""

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