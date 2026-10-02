"""SearchProvider — implements FactProviderPort via DuckDuckGo's Instant
Answer API (free, no API key).

★ new — second reference implementation of the fact-lookup pattern
(alongside weather.py), proving the registry needs zero changes to add a
category. Instant Answer only returns a result for queries DuckDuckGo can
resolve directly (definitions, disambiguation, known entities) — it is
NOT a general web search API, so `text` may legitimately be empty for
open-ended queries.
"""

from __future__ import annotations

from datetime import datetime, timezone

from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery
from tpa.online.http_client import AllowListedHttpClient

_HOST = "api.duckduckgo.com"


class SearchProvider:
    """Fetches a DuckDuckGo Instant Answer for a query string."""

    category = "search"
    allowed_hosts = (_HOST,)

    def __init__(self, http_client: AllowListedHttpClient):
        self._http = http_client

    async def fetch(self, query: FactQuery) -> FactAnswer:
        q = query.params.get("query")
        if not q:
            raise ValueError("search queries require a 'query' param")

        data = await self._http.get(
            f"https://{_HOST}/",
            category=self.category,
            params={"q": q, "format": "json", "no_html": "1", "skip_disambig": "1"},
        )
        text = data.get("AbstractText") or ""
        if not text:
            related = data.get("RelatedTopics") or []
            first = related[0] if related else {}
            text = first.get("Text", "") if isinstance(first, dict) else ""
        if not text:
            text = f"I couldn't find a direct answer for \"{q}\"."

        source_url = data.get("AbstractURL") or ""
        sources = (source_url,) if source_url else (_HOST,)
        return FactAnswer(provider_id="duckduckgo", category=self.category, text=text, sources=sources, fetched_at=datetime.now(timezone.utc))