"""WeatherProvider – implements FactProviderPort via Open-Meteo (free, no API key).

* New, PS-mandatory (REQ-M-09). Reference implementation for the
pluggable fact-lookup registry – adding a new category means writing one
file shaped exactly like this one, per the invariant established in
service/lookup/registry.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery
from tpa.online.http_client import AllowListedHttpClient

_HOST = "api.open-meteo.com"


class WeatherProvider:
    """Fetches current weather for a lat/lon from Open-Meteo."""

    category = "weather"
    allowed_hosts = (_HOST,)

    def __init__(self, http_client: AllowListedHttpClient):
        self._http = http_client

    async def fetch(self, query: FactQuery) -> FactAnswer:
        lat = query.params.get("lat")
        lon = query.params.get("lon")
        if lat is None or lon is None:
            raise ValueError("weather queries require 'lat' and 'lon' params")

        data = await self._http.get(
            f"https://{_HOST}/v1/forecast",
            category=self.category,
            params={"latitude": lat, "longitude": lon, "current_weather": "true"},
        )
        current = data.get("current_weather", {})
        temp = current.get("temperature")
        wind = current.get("windspeed")
        text = f"It's about {temp}°C with wind around {wind} km/h right now." if temp is not None else "I couldn't get a weather reading."

        return FactAnswer(provider_id="open-meteo", category=self.category, text=text, sources=(_HOST,), fetched_at=datetime.now(timezone.utc))
