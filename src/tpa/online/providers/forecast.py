"""ForecastProvider — implements FactProviderPort: a daily forecast 0-6 days ahead via Open-Meteo (free, no key).

Same host and the same `/v1/forecast` endpoint as WeatherProvider, asking for `daily=` fields instead of `current=`.
The day is chosen by `date_offset` (0 = today, 1 = tomorrow); the date arithmetic happens here and in the provider's
own calendar, never in the model.
"""

from __future__ import annotations

from datetime import datetime, timezone

from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery
from domain.policies.weather_code_policy import describe_weather_code
from exceptions.exception import ToolUnavailableError
from tpa.online.http_client import AllowListedHttpClient

_HOST = "api.open-meteo.com"
_DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
MAX_DAYS_AHEAD = 6


class ForecastProvider:
    category = "forecast"
    allowed_hosts = (_HOST,)

    def __init__(self, http_client: AllowListedHttpClient):
        self._http = http_client

    async def fetch(self, query: FactQuery) -> FactAnswer:
        try:
            lat = float(query.params["lat"])
            lon = float(query.params["lon"])
            offset = int(query.params["date_offset"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("forecast queries require numeric 'lat', 'lon' and integer 'date_offset' params") from None
        if not 0 <= offset <= MAX_DAYS_AHEAD:
            raise ValueError(f"date_offset must be 0..{MAX_DAYS_AHEAD}")

        payload = await self._http.get(
            f"https://{_HOST}/v1/forecast",
            category=self.category,
            params={"latitude": lat, "longitude": lon, "daily": _DAILY, "forecast_days": offset + 1, "timezone": "auto"},
        )
        daily = payload.get("daily") or {}
        try:
            day = str(daily["time"][offset])
            t_max, t_min = daily["temperature_2m_max"][offset], daily["temperature_2m_min"][offset]
            code = daily["weather_code"][offset]
            rain = (daily.get("precipitation_probability_max") or [None] * (offset + 1))[offset]
        except (KeyError, IndexError, TypeError):
            raise ToolUnavailableError("ForecastProvider", self.category, f"{_HOST} returned no forecast for day {offset}") from None
        if t_max is None or t_min is None:
            raise ToolUnavailableError("ForecastProvider", self.category, f"{_HOST} returned no temperatures for day {offset}")

        condition = describe_weather_code(code)
        data = {
            "date": day, "date_offset": offset, "condition": condition, "temp_max_c": t_max, "temp_min_c": t_min,
            "rain_chance_pct": rain, "timezone": payload.get("timezone", ""), "as_of": day,
        }
        text = f"{day}: {condition}, {t_min} to {t_max} C" + (f", {rain}% chance of rain." if rain is not None else ".")
        return FactAnswer(
            provider_id="open-meteo", category=self.category, text=text, sources=(_HOST,),
            fetched_at=datetime.now(timezone.utc), data=data,
        )
