"""TavilyProvider — real web search via the Tavily API (built for LLM use).

Replaces the DuckDuckGo Instant Answer provider, which only resolved
encyclopedic entities and returned nothing for news or "latest" questions.

Privacy: only the model-chosen search query leaves the device — never
conversation history, audio or user facts (FactProviderPort contract). The API
key is read from the environment at call time through an injected callable
and is never logged; a missing key raises ToolUnavailableError(not_configured)
without making any network call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urlparse

from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery
from exceptions.exception import ToolUnavailableError
from tpa.online.http_client import AllowListedHttpClient

_HOST = "api.tavily.com"
_MAX_QUERY = 200
_SNIPPET_CHARS = 200
_MAX_HITS = 3


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _iso_date(raw) -> str:
    """Tavily gives RFC-2822 ("Sun, 27 Sep 2026 18:00:00 GMT") or ISO dates; return YYYY-MM-DD or ''."""
    raw = str(raw or "").strip()
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


class TavilyProvider:
    category = "search"
    allowed_hosts = (_HOST,)

    def __init__(self, http_client: AllowListedHttpClient, api_key_provider: Callable[[], str | None]):
        self._http = http_client
        self._api_key = api_key_provider

    def is_configured(self) -> bool:
        return bool((self._api_key() or "").strip())

    async def fetch(self, query: FactQuery) -> FactAnswer:
        q = str(query.params.get("query") or "").strip()[:_MAX_QUERY]
        if not q:
            raise ValueError("search queries require a 'query' param")
        key = (self._api_key() or "").strip()
        if not key:
            raise ToolUnavailableError(
                "TavilyProvider", self.category, "web search is not set up (TAVILY_API_KEY is missing)",
                not_configured=True,
            )
        topic = "news" if str(query.params.get("topic") or "").lower() == "news" else "general"
        depth = "advanced" if str(query.params.get("depth") or "").lower() == "advanced" else "basic"
        body: dict = {
            "query": q, "topic": topic, "search_depth": depth, "max_results": _MAX_HITS,
            "include_answer": True, "include_raw_content": False,
        }
        if topic == "news":
            body["time_range"] = "week"

        payload = await self._http.post_json(
            f"https://{_HOST}/search", category=self.category, json=body,
            headers={"Authorization": f"Bearer {key}"},
        )
        answer = _clip(payload.get("answer") or "", 400)
        hits = []
        for r in (payload.get("results") or [])[:_MAX_HITS]:
            hits.append({
                "title": _clip(r.get("title") or "", 120),
                "domain": (urlparse(r.get("url") or "").hostname or "").removeprefix("www."),
                "snippet": _clip(r.get("content") or "", _SNIPPET_CHARS),
                # longer text, never spoken or shown: only used to judge whether the hit is on topic
                "match_text": _clip(f"{r.get('title') or ''} {r.get('content') or ''}", 900),
                "published": _iso_date(r.get("published_date")),
            })
        lines = []
        if answer:
            lines.append(f"ANSWER: {answer}")
        for i, h in enumerate(hits, 1):
            when = f", {h['published']}" if h["published"] else ""
            lines.append(f"{i}. {h['title']} ({h['domain']}{when}): {h['snippet']}")
        return FactAnswer(
            provider_id="tavily", category=self.category, text="\n".join(lines) or "no results",
            sources=tuple(dict.fromkeys(h["domain"] for h in hits if h["domain"])) or (_HOST,),
            fetched_at=datetime.now(timezone.utc),
            data={"query": q, "topic": topic, "depth": depth, "answer": answer, "hits": hits},
        )
