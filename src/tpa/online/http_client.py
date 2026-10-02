"""AllowListedHttpClient – the ONLY module in this codebase allowed to make
outbound internet HTTP calls.

* New, PS-mandatory (REQ-M-06/M-09). Enforces domain.policies.egress_policy
on every single request before it leaves the process. `localhost`/`127.0.0.1`
(the local inference server) bypass the check entirely, matching
egress_policy's own `_ALWAYS_ALLOWED_HOSTS` rule.
"""

from __future__ import annotations

from urllib.parse import urlparse

from domain.policies.egress_policy import is_allowed
from domain.value_objects.egress_target import EgressTarget


class EgressDeniedError(RuntimeError):
    pass


class AllowListedHttpClient:
    """A `httpx`-backed client that refuses any request to a non-allow-listed host."""

    def __init__(self, allow_list: frozenset[str], timeout: float = 10.0):
        self._allow_list = allow_list
        self._timeout = timeout

    def _check(self, url: str, category: str) -> None:
        host = urlparse(url).hostname or ""
        target = EgressTarget(host=host, category=category)
        if not is_allowed(target, self._allow_list):
            raise EgressDeniedError(f"outbound request to '{host}' (category={category!r}) denied – not on the allow-list")

    async def get(self, url: str, *, category: str, params: dict | None = None) -> dict:
        self._check(url, category)
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
