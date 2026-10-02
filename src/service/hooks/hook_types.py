"""BaseHook — abstract base class for event hooks.

Donor: veda/meta/hook.py, read in full and ported verbatim.
"""

from abc import ABC, abstractmethod
from typing import Any

from domain.entities.agent_context import AgentContext


class BaseHook(ABC):
    """Abstract base class for event hooks.

    Hooks run at defined points in the agent/skill lifecycle:
    pre_skill (return False to abort), post_skill, on_error,
    on_agent_start, on_agent_complete, on_approval_needed.
    """

    def __init__(self, name: str, event: str):
        self.name = name
        self.event = event

    @abstractmethod
    async def execute(self, ctx: AgentContext, **kwargs: Any) -> bool | None:
        """Execute the hook. For pre_* events, return False to abort."""