"""FxProvider — implements FactProviderPort via the Frankfurter API
(free, no API key, ECB reference rates).

★ new — third reference implementation, demonstrating a category
(exchange rates) with two required params instead of weather's lat/lon.
"""

from __future__ import annotations

from datetime import datetime, timezone

from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery
from tpa.online.http_client import AllowListedHttpClient

_HOST = "api.frankfurter.app"


class FxProvider:
    """Fetches a current exchange rate between two currency codes."""

    category = "fx"
    allowed_hosts = (_HOST,)

    def __init__(self, http_client: AllowListedHttpClient):
        self._http = http_client

    async def fetch(self, query: FactQuery) -> FactAnswer:
        base = (query.params.get("from") or "").upper()
        target = (query.params.get("to") or "").upper()
        if not base or not target:
            raise ValueError("fx queries require 'from' and 'to' currency-code params")

        data = await self._http.get(
            f"https://{_HOST}/latest",
            category=self.category,
            params={"from": base, "to": target},
        )
        rate = (data.get("rates") or {}).get(target)
        text = f"1 {base} is about {rate} {target} right now." if rate is not None else f"I couldn't get a rate for {base}/{target}."

        return FactAnswer(provider_id="frankfurter", category=self.category, text=text, sources=(_HOST,), fetched_at=datetime.now(timezone.utc))