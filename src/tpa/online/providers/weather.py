"""WeatherProvider — implements FactProviderPort via Open-Meteo (free, no API key).

★ New, PS-mandatory (REQ-M-09). Returns *structured* current conditions in
`FactAnswer.data` (temperature, feels-like, humidity, wind, WMO condition,
local observation time) plus a compact fact-only `text`. The place name ->
coordinates step is a separate provider (geocode.py) so this one stays a
pure lat/lon -> reading adapter.
"""

from __future__ import annotations

from datetime import datetime, timezone

from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery
from domain.policies.weather_code_policy import describe_weather_code
from exceptions.exception import ToolUnavailableError
from tpa.online.http_client import AllowListedHttpClient

_HOST = "api.open-meteo.com"
_CURRENT = "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,is_day"


class WeatherProvider:
    """Fetches current weather for a lat/lon from Open-Meteo."""

    category = "weather"
    allowed_hosts = (_HOST,)

    def __init__(self, http_client: AllowListedHttpClient):
        self._http = http_client

    async def fetch(self, query: FactQuery) -> FactAnswer:
        try:
            lat = float(query.params["lat"])
            lon = float(query.params["lon"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("weather queries require numeric 'lat' and 'lon' params") from None

        payload = await self._http.get(
            f"https://{_HOST}/v1/forecast",
            category=self.category,
            params={"latitude": lat, "longitude": lon, "current": _CURRENT, "timezone": "auto"},
        )
        current = payload.get("current") or {}
        temp = current.get("temperature_2m")
        if temp is None:
            raise ToolUnavailableError("WeatherProvider", self.category, f"{_HOST} returned no current reading")

        condition = describe_weather_code(current.get("weather_code"))
        feels = current.get("apparent_temperature")
        humidity = current.get("relative_humidity_2m")
        wind = current.get("wind_speed_10m")
        as_of = str(current.get("time") or "")  # local time at the place, e.g. 2026-10-03T19:30
        data = {
            "temperature_c": temp, "feels_like_c": feels, "humidity_pct": humidity, "wind_kmh": wind,
            "condition": condition, "is_day": bool(current.get("is_day")), "as_of": as_of,
            "timezone": payload.get("timezone", ""),
        }
        parts = [f"{condition}", f"{temp} C" + (f" (feels like {feels} C)" if feels is not None else "")]
        if humidity is not None:
            parts.append(f"humidity {humidity}%")
        if wind is not None:
            parts.append(f"wind {wind} km/h")
        text = ", ".join(parts) + (f". Local time {as_of[11:16]}." if len(as_of) >= 16 else ".")
        return FactAnswer(
            provider_id="open-meteo", category=self.category, text=text, sources=(_HOST,),
            fetched_at=datetime.now(timezone.utc), data=data,
        )
