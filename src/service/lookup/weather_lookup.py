"""WeatherLookup — current weather for a named place, with a search fallback.

place name -> PlaceResolver -> Open-Meteo reading -> ToolObservation. If the
weather provider is down, falls back to a web search for the conditions and
labels the result as approximate so the user hears where it came from. If both
fail, the original error is raised and the user is told plainly.
"""

from __future__ import annotations

from datetime import datetime

from core.enums import ErrorMessage, ExceptionCode
from core.logging_config import logger
from domain.entities.fact_query import FactQuery
from domain.entities.place import Place
from domain.entities.tool_observation import ToolObservation
from exceptions.exception import AppException, ToolUnavailableError
from service.lookup.lookup_service import LookupService
from service.lookup.place_resolver import PlaceResolver

MAX_DAYS_AHEAD = 6  # Open-Meteo's free forecast window we expose; mirrored by the provider's own check


def _num(value) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def _day_label(offset: int, iso_date: str) -> str:
    if offset == 0:
        return "today"
    if offset == 1:
        return "tomorrow"
    try:
        return "on " + datetime.strptime(iso_date, "%Y-%m-%d").strftime("%A")
    except ValueError:
        return f"in {offset} days"


class WeatherLookup:
    def __init__(self, lookup: LookupService, places: PlaceResolver):
        self._lookup = lookup
        self._places = places

    async def get(self, place_name: str | None) -> ToolObservation:
        place = await self._places.resolve(place_name)
        try:
            answer = await self._lookup.fetch(
                FactQuery(category="weather", params={"lat": place.lat, "lon": place.lon})
            )
        except ToolUnavailableError as primary:
            logger.warning(f"[weather] provider failed ({primary}); trying web search fallback")
            fallback = await self._search_fallback(place)
            if fallback is None:
                raise primary
            return fallback

        d = answer.data
        as_of = d.get("as_of", "")
        spoken = (
            f"In {place.name} it's {round(d['temperature_c'])} degrees and {d['condition']}"
            + (f", feels like {round(d['feels_like_c'])}" if d.get("feels_like_c") is not None else "")
            + "."
        )
        extras = []
        if d.get("humidity_pct") is not None:
            extras.append(f"humidity {round(d['humidity_pct'])} percent")
        if d.get("wind_kmh") is not None:
            extras.append(f"wind {round(d['wind_kmh'])} kilometres an hour")
        if extras:
            spoken += " " + ", ".join(extras).capitalize() + "."
        text = f"WEATHER for {place.label()} (source {answer.provider_id}{', cached' if answer.cached else ''}): {answer.text}"
        return ToolObservation(
            text=text, spoken=spoken, source=answer.provider_id, as_of=as_of, cached=answer.cached,
            data={**d, "place": place.label(), "place_source": place.source},
        )

    async def forecast(self, place_name: str | None, date_offset) -> ToolObservation:
        """Daily forecast 0-6 days ahead. The model only picks the offset; range checks and the day name are code."""
        if isinstance(date_offset, bool) or not isinstance(date_offset, int) or not 0 <= date_offset <= MAX_DAYS_AHEAD:
            raise AppException(
                class_name="WeatherLookup", code=ExceptionCode.VALIDATION_ERROR, error_message=ErrorMessage.GENERIC,
                detail=f"I can only forecast 0 to {MAX_DAYS_AHEAD} days ahead",
            )
        place = await self._places.resolve(place_name)
        answer = await self._lookup.fetch(
            FactQuery(category="forecast", params={"lat": place.lat, "lon": place.lon, "date_offset": date_offset})
        )
        d = answer.data
        spoken = f"In {place.name} {_day_label(date_offset, d.get('date', ''))}, {d['condition']}"
        if d.get("rain_chance_pct") is not None:
            spoken += f", {round(d['rain_chance_pct'])} percent chance of rain"
        spoken += f", {round(d['temp_min_c'])} to {round(d['temp_max_c'])} degrees."
        text = f"FORECAST for {place.label()} (source {answer.provider_id}{', cached' if answer.cached else ''}): {answer.text}"
        return ToolObservation(
            text=text, spoken=spoken, source=answer.provider_id, as_of=d.get("as_of", ""), cached=answer.cached,
            data={**d, "place": place.label(), "place_source": place.source},
        )

    async def _search_fallback(self, place: Place) -> ToolObservation | None:
        if self._lookup.registry.get("search") is None:
            return None
        try:
            answer = await self._lookup.fetch(
                FactQuery(category="search", params={"query": f"current weather in {place.label()}"})
            )
        except Exception as e:
            logger.warning(f"[weather] search fallback failed: {e}")
            return None
        said = answer.data.get("answer") or (answer.data.get("hits") or [{}])[0].get("snippet", "")
        if not said:
            return None
        return ToolObservation(
            text=f"WEATHER for {place.label()} (source web search, approximate): {said}",
            spoken=f"The weather service didn't answer, but a web search says: {said}",
            source="tavily (web search, approximate)", as_of="", data={"place": place.label(), "approximate": True},
        )
