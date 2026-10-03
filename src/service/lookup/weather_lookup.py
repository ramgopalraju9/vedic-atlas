"""WeatherLookup — current weather for a named place, with a search fallback.

place name -> PlaceResolver -> Open-Meteo reading -> ToolObservation. If the
weather provider is down, falls back to a web search for the conditions and
labels the result as approximate so the user hears where it came from. If both
fail, the original error is raised and the user is told plainly.
"""

from __future__ import annotations

from core.logging_config import logger
from domain.entities.fact_query import FactQuery
from domain.entities.place import Place
from domain.entities.tool_observation import ToolObservation
from exceptions.exception import ToolUnavailableError
from service.lookup.lookup_service import LookupService
from service.lookup.place_resolver import PlaceResolver


def _num(value) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


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
