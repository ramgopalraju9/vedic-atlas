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