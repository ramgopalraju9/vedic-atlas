"""allow_list – the configured set of hosts permitted for outbound egress.

* New, PS-mandatory. Single source of truth read by both
AllowListedHttpClient (transport-layer enforcement) and
FactProviderRegistry (boot-time provider validation, service/lookup/registry.py)
so the two can never disagree about which hosts are permitted.
"""

DEFAULT_ALLOW_LIST: frozenset[str] = frozenset({
    "localhost",
    "127.0.0.1",
    "api.open-meteo.com",      # weather
    "api.duckduckgo.com",      # search
    "gdeltproject.org",        # news
})
