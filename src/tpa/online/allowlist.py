"""allow_list — the configured set of hosts permitted for outbound egress.

★ New, PS-mandatory. Single source of truth read by both
AllowListedHttpClient (transport-layer enforcement) and
FactProviderRegistry (boot-time provider validation, service/lookup/registry.py)
so the two can never disagree about which hosts are permitted.
"""

DEFAULT_ALLOW_LIST: frozenset[str] = frozenset({
    "localhost",
    "127.0.0.1",
    "api.open-meteo.com",       # weather
    "geocoding-api.open-meteo.com",  # place name -> coordinates
    "api.tavily.com",           # web search
    "api.frankfurter.dev",      # fx (ECB reference rates)
    "open.er-api.com",          # fx fallback (166 currencies)
    "gdeltproject.org",         # news
    "oauth2.googleapis.com",    # Google token refresh
    "gmail.googleapis.com",     # Gmail
    "www.googleapis.com",       # Google Calendar
})