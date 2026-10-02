"""BaseGuardrail — abstract base class for guardrails.

Donor: veda/meta/guardrail.py, read in full and ported verbatim.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from domain.entities.agent_context import AgentContext


@dataclass
class GuardrailResult:
    """The result of a guardrail validation check."""

    passed: bool
    reason: str = ""
    blocked_content: str = ""


class BaseGuardrail(ABC):
    """Abstract base class for guardrails — validates inputs/outputs, enforces policy."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def validate(self, ctx: AgentContext, **kwargs) -> GuardrailResult:
        """Run the check. Return a GuardrailResult."""