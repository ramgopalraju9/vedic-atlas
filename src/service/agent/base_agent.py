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
    ):
        self.name = name
        self.description = description
        self.skills = skills or []
        self.system_prompt = system_prompt
        self.model = model

    @abstractmethod
    async def execute(self, ctx: AgentContext) -> AgentResult:
        """Process a request and return a full result."""

    @abstractmethod
    async def execute_stream(
        self, ctx: AgentContext, cancel_event: asyncio.Event | None = None
    ) -> AsyncIterator[str]:
        """Process a request, yielding response text as it becomes available."""