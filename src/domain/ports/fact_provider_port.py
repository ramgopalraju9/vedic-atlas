from typing import Protocol, runtime_checkable
from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery

@runtime_checkable
class FactProviderPort(Protocol):
    """A single current-public-fact lookup category, fact-only."""

    @property
    def category(self) -> str:
        """Registry key, e.g. 'weather', 'stocks'."""

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """Hosts this provider may contact. Enforced by egress_guard."""
        ...

    async def fetch(self, query: FactQuery) -> FactAnswer:
        """Answer one query. Must never receive or transmit conversation content."""
        ...
