"""AgentProfile — the declared identity of a registered agent.

Donor: veda/config.py's AgentEntry (`enabled`, `model`, `description`,
`skills`, `system_prompt_extra`). AgentEntry is a Pydantic config-loading
model (ring 2/3 concern — how it's read from YAML); AgentProfile is the
domain concept of "who this agent is", used by the registry and the
supervisor's routing policy independent of how it was configured.

`model` renamed to `model_alias` to match the alias vocabulary from
ADR-002 (`sonnet` / `haiku` as capability tiers, not vendor model names).
"""

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