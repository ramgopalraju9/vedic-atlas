"""ToolCall — a single tool invocation requested by the model.

New (Feature C). Dependency-free value object so the domain layer stays
free of the skill runtime.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolCall:
    """A model-requested skill invocation: a name and its arguments."""

    name: str
    args: dict = field(default_factory=dict)