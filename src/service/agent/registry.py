from core.enums import ErrorMessage, ExceptionCode
from exceptions.exception import AppException
from core.logging_config import logger
from service.agent.base_agent import BaseAgent


class AgentRegistry:
    """Registry for agents available to the supervisor for routing."""

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        if agent.name in self._agents:
            logger.warning(f"Agent '{agent.name}' already registered, overwriting")
        self._agents[agent.name] = agent
        logger.info(f"Registered agent: {agent.name}")

    def get(self, name: str) -> BaseAgent:
        if name not in self._agents:
            raise AppException(
                class_name="AgentRegistry",
                code=ExceptionCode.AGENT_ROUTING_ERROR,
                error_message=ErrorMessage.AGENT_ROUTING_FAILED,
                detail=f"Agent '{name}' is not registered",
            )
        return self._agents[name]

    def is_registered(self, name: str) -> bool:
        return name in self._agents

    def list_all(self) -> list[BaseAgent]:
        return list(self._agents.values())

    def list_routable(self, exclude: set[str] | None = None) -> list[BaseAgent]:
        """Agents eligible for supervisor routing (excludes the supervisor itself)."""
        ex = exclude or set()
        return [a for a in self._agents.values() if a.name not in ex]

    @property
    def count(self) -> int:
        return len(self._agents)