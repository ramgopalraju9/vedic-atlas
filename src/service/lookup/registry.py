"""FactProviderRegistry - the pluggable adapter registry for current-public-fact lookups.

* NEW, PS-mandatory. Spec already written in docs/migration/FILE_MAP.md and
  TARGET_AGENT_PROMPT.md ($ "service/lookup/") before this file was built -
  no donor equivalent exists. Refuses at construction time to enable any
  provider whose declared `allowed_hosts` are not a subset of the configured
  network allow-list, so a misconfigured provider fails at boot, not at
  first use.
"""

from domain.ports.fact_provider_port import FactProviderPort


class FactProviderRegistry:
    def __init__(self, allow_list: frozenset[str]):
        self.allow_list = allow_list
        self._providers: dict[str, FactProviderPort] = {}

    def register(self, provider: FactProviderPort) -> None:
        missing = set(provider.allowed_hosts) - self.allow_list
        if missing:
            raise ValueError(
                f"FactProvider '{provider.category}' declares hosts {sorted(missing)} "
                "not present in the configured network allow-list - refusing to register"
            )
        self._providers[provider.category] = provider

    def get(self, category: str) -> FactProviderPort | None:
        return self._providers.get(category)

    def list_enabled(self) -> list[str]:
        return list(self._providers.keys())