from dataclasses import dataclass, field

@dataclass
class AgentProfile:
    """Declared identity of one registered agent."""
    name: str
    description: str
    model_alias: str
    skills: tuple[str, ...] = field(default_factory=tuple)
    system_prompt_extra: str = ""
    enabled: bool = True
