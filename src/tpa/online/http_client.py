"""AllowListedHttpClient — the ONLY module in this codebase allowed to make
outbound internet HTTP calls.

★ New, PS-mandatory (REQ-M-06/M-09). Enforces domain.policies.egress_policy
on every single request before it leaves the process. `localhost`/`127.0.0.1`
(the local inference server) bypass the check entirely, matching
egress_policy's own `_ALWAYS_ALLOWED_HOSTS` rule.

Every request is logged as `[http] category= host= status= ms=` (never headers
or bodies — those can carry API keys and user text). Upstream failures are
normalised to ToolUnavailableError so callers handle one error type; redirects
are NOT followed, because a redirect could leave the allow-list.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from core.logging_config import logger
from domain.policies.egress_policy import is_allowed
from domain.value_objects.egress_target import EgressTarget
from exceptions.exception import ToolUnavailableError


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
            raise EgressDeniedError(f"outbound request to '{host}' (category={category!r}) denied — not on the allow-list")

    async def get(self, url: str, *, category: str, params: dict | None = None, headers: dict | None = None) -> dict:
        return await self._request("GET", url, category=category, params=params, headers=headers)

    async def post_json(self, url: str, *, category: str, json: dict, headers: dict | None = None) -> dict:
        return await self._request("POST", url, category=category, json=json, headers=headers)

    async def _request(self, method: str, url: str, *, category: str, **kwargs) -> dict:
        self._check(url, category)
        import httpx

        host = urlparse(url).hostname or ""
        started = time.perf_counter()
        status: int | str = "-"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(method, url, **kwargs)
                status = resp.status_code
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            raise ToolUnavailableError(
                "AllowListedHttpClient", category, f"{host} answered HTTP {e.response.status_code}",
                status=e.response.status_code,
            ) from e
        except httpx.TimeoutException as e:
            status = "timeout"
            raise ToolUnavailableError("AllowListedHttpClient", category, f"{host} timed out") from e
        except (httpx.HTTPError, ValueError) as e:
            status = "error"
            raise ToolUnavailableError(
                "AllowListedHttpClient", category, f"{host} unreachable ({type(e).__name__})"
            ) from e
        finally:
            logger.info(
                f"[http] category={category} host={host} status={status} ms={int((time.perf_counter() - started) * 1000)}"
            )
