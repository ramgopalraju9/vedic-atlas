"""LookupService — single entry point for current-public-fact lookups.

★ NEW, PS-mandatory (REQ-M-09). Calls the egress allow/deny decision
BEFORE dispatching to any provider. The full service/privacy/egress_guard.py
(with audit/event publishing) is a later batch; this calls
domain.policies.egress_policy directly for now — a real, working
enforcement point, just without the audit trail wiring yet. When the
privacy batch lands, egress_guard.py can wrap this same call without
requiring lookup_service.py to change.
"""

from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery
from domain.policies.egress_policy import is_allowed
from domain.value_objects.egress_target import EgressTarget
from service.lookup.registry import FactProviderRegistry


class EgressDeniedError(RuntimeError):
    pass


class LookupService:
    def __init__(self, registry: FactProviderRegistry, allow_list: frozenset[str]):
        self.registry = registry
        self.allow_list = allow_list

    async def fetch(self, query: FactQuery) -> FactAnswer:
        provider = self.registry.get(query.category)
        if provider is None:
            raise ValueError(f"no fact provider registered for category '{query.category}'")

        for host in provider.allowed_hosts:
            target = EgressTarget(host=host, category=query.category)
            if not is_allowed(target, self.allow_list):
                raise EgressDeniedError(f"host '{host}' for category '{query.category}' is not on the allow-list")

        return await provider.fetch(query)