"""ProviderHealth — the result of probing one online tool dependency.

Pure data. Produced by service/lookup/health_service.py, rendered by
`veda doctor` and GET /api/lookup/health so "are my weather / search /
currency tools actually working?" has a one-command answer.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderHealth:
    """One probe outcome."""

    name: str  # e.g. "weather", "geocoding", "web_search"
    ok: bool
    configured: bool  # False when a required secret (API key) is missing
    latency_ms: int
    detail: str  # a sample answer on success, the failure reason otherwise
