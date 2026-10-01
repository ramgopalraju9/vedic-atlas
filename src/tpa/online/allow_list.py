"""allow_list - the configured set of allowed domains/hosts.

* New, PS-mandatory. Single source of truth shared between
AllowListedHttpClient (transport-level enforcement) and
FactProviderRegistry (boot-time provider validation)
so the two can never disagree about what is allowed.
"""

DEFAULT_ALLOW_LIST: frozenset[str] = frozenset({
    "localhost",
    "127.0.0.1",
    "api.open-meteo.com",      # weather
    "api.duckduckgo.com",      # search
    "gdeltproject.org",        # news
})
