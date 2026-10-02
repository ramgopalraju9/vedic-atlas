"""FactProviderPort — one adapter per current-public-fact category.

New, PS-mandatory (REQ-M-09). Already specified in docs/migration/FILE_MAP.md
and docs/migration/TARGET_AGENT_PROMPT.md — this is the concrete write-up of
that spec. `allowed_hosts` lets service/lookup/registry.py refuse at boot
to enable any provider whose hosts aren't on the configured network
allow-list, without the registry needing to know anything about a given
category's implementation.

The category set (weather, search, news, stocks, flights, traffic, sports,
fx, ...) is intentionally NOT enumerated anywhere near this Protocol —
adding a category is one new adapter file, with zero changes here.
"""

from typing import Protocol, runtime_checkable

from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery


@runtime_checkable
class FactProviderPort(Protocol):
    """A single current-public-fact lookup category, fact-only."""

    @property
    def category(self) -> str:
        """Registry key, e.g. "weather", "stocks"."""
        ...

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """Hosts this provider may contact. Enforced by egress_guard."""
        ...

    async def fetch(self, query: FactQuery) -> FactAnswer:
        """Answer one query. Must never receive or transmit conversation content."""
        ...