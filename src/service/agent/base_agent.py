"""BaseAgent — abstract base class for all agents.

Donor: veda/meta/agent.py's BaseAgent ABC, read in full during Batch 2
grounding (AgentResult, the sibling class in this donor file, already
landed at domain/entities/agent_result.py). Ported verbatim — this is a
service-layer base class (has behaviour: `execute`/`execute_stream` are
abstract methods agents implement), not a Port.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import AsyncIterator

from domain.entities.agent_context import AgentContext
from domain.entities.agent_result import AgentResult


class BaseAgent(ABC):
    """Abstract base class for all agents.

    Each agent has a name, description (for supervisor routing), a list of
    skill names it can invoke, a system prompt, and a model preference.
    """

    def __init__(
        self,
        name: str,
        description: str,
        skills: list[str] | None = None,
        system_prompt: str = "",
        model: str | None = None,
        triggers: list[str] | None = None,
    ):
        self.name = name
        self.description = description
        self.skills = skills or []
        self.system_prompt = system_prompt
        self.model = model
        self.triggers = triggers or []  # regexes for deterministic routing (see routing_policy)

    @abstractmethod
    async def execute(self, ctx: AgentContext) -> AgentResult:
        """Process a request and return a full result."""

    @abstractmethod
    async def execute_stream(
        self, ctx: AgentContext, cancel_event: asyncio.Event | None = None
    ) -> AsyncIterator[str]:
        """Process a request, yielding response text as it becomes available."""