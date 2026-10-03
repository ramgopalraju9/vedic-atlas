"""GeocodingProvider — place name -> coordinates via Open-Meteo's geocoder (free, no key).

Returns every candidate (ranked by the service, best first) in
`FactAnswer.data["places"]` so the caller can see an ambiguous name
("Hyderabad" in India vs Pakistan) and the spoken answer can name the one used.
"""

from __future__ import annotations

from datetime import datetime, timezone

from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery
from tpa.online.http_client import AllowListedHttpClient

_HOST = "geocoding-api.open-meteo.com"
_MAX_NAME = 100


class GeocodingProvider:
    category = "geocode"
    allowed_hosts = (_HOST,)

    def __init__(self, http_client: AllowListedHttpClient):
        self._http = http_client

    async def fetch(self, query: FactQuery) -> FactAnswer:
        name = str(query.params.get("name") or "").strip()[:_MAX_NAME]
        if not name:
            raise ValueError("geocode queries require a 'name' param")

        payload = await self._http.get(
            f"https://{_HOST}/v1/search",
            category=self.category,
            params={"name": name, "count": 5, "language": "en", "format": "json"},
        )
        places = [
            {
                "name": r.get("name", ""),
                "region": r.get("admin1", ""),
                "country": r.get("country", ""),
                "lat": r["latitude"],
                "lon": r["longitude"],
                "population": r.get("population") or 0,
                "timezone": r.get("timezone", ""),
            }
            for r in (payload.get("results") or [])
            if "latitude" in r and "longitude" in r
        ]
        text = "; ".join(
            ", ".join(p for p in (pl["name"], pl["region"], pl["country"]) if p) for pl in places
        ) or f"no place found for '{name}'"
        return FactAnswer(
            provider_id="open-meteo-geocoding", category=self.category, text=text, sources=(_HOST,),
            fetched_at=datetime.now(timezone.utc), data={"places": places},
        )
