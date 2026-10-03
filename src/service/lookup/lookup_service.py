"""LookupService — single entry point for current-public-fact lookups.

★ NEW, PS-mandatory (REQ-M-09). Calls the egress allow/deny decision
BEFORE dispatching to any provider. The full service/privacy/egress_guard.py
(with audit/event publishing) is a later batch; this calls
domain.policies.egress_policy directly for now — a real, working
enforcement point, just without the audit trail wiring yet. When the
privacy batch lands, egress_guard.py can wrap this same call without
requiring lookup_service.py to change.

Optionally caches answers per category (TTL from the tool manifests). Every
lookup is logged as `[lookup] category= cache=hit|miss ms= provider=`.
"""

from __future__ import annotations

import dataclasses
import json
import time

from core.logging_config import logger
from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery
from domain.policies.egress_policy import is_allowed
from domain.value_objects.egress_target import EgressTarget
from service.lookup.registry import FactProviderRegistry
from service.lookup.ttl_cache import TtlCache


class EgressDeniedError(RuntimeError):
    pass


class LookupService:
    def __init__(
        self,
        registry: FactProviderRegistry,
        allow_list: frozenset[str],
        cache: TtlCache | None = None,
        ttl_by_category: dict[str, int] | None = None,
    ):
        self.registry = registry
        self.allow_list = allow_list
        self._cache = cache
        self._ttl = ttl_by_category or {}

    async def fetch(self, query: FactQuery) -> FactAnswer:
        provider = self.registry.get(query.category)
        if provider is None:
            raise ValueError(f"no fact provider registered for category '{query.category}'")

        for host in provider.allowed_hosts:
            target = EgressTarget(host=host, category=query.category)
            if not is_allowed(target, self.allow_list):
                raise EgressDeniedError(f"host '{host}' for category '{query.category}' is not on the allow-list")

        ttl = self._ttl.get(query.category, 0)
        key = (query.category, json.dumps(query.params, sort_keys=True, default=str))
        if self._cache is not None and ttl > 0:
            hit = self._cache.get(key)
            if hit is not None:
                logger.info(f"[lookup] category={query.category} cache=hit provider={hit.provider_id}")
                return dataclasses.replace(hit, cached=True)

        started = time.perf_counter()
        answer = await provider.fetch(query)
        logger.info(
            f"[lookup] category={query.category} cache=miss ms={int((time.perf_counter() - started) * 1000)} "
            f"provider={answer.provider_id}"
        )
        if self._cache is not None and ttl > 0:
            self._cache.put(key, answer, ttl)
        return answer
