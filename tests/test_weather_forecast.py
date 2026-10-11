"""Forecast tool (docs/implementation_guide.md Phase 4.1): date_offset range, spoken day + place, provider shape."""

import pytest

from domain.entities.fact_query import FactQuery
from exceptions.exception import AppException, ToolUnavailableError
from service.lookup.lookup_service import LookupService
from service.lookup.place_resolver import PlaceResolver
from service.lookup.weather_lookup import WeatherLookup
from service.skills.builtin.weather import GetWeatherForecastSkill
from tpa.online.providers.forecast import ForecastProvider
from tpa.online.providers.geocode import GeocodingProvider
from service.lookup.registry import FactProviderRegistry
from test_lookup_tools import ALLOW, GEO_OK, MANIFESTS, FakeHttp, _ctx, run

DAILY_OK = {"timezone": "Asia/Kolkata", "daily": {
    "time": ["2026-10-07", "2026-10-08", "2026-10-09"],
    "weather_code": [0, 63, 3],
    "temperature_2m_max": [31.0, 24.4, 28.0],
    "temperature_2m_min": [22.0, 19.2, 21.0],
    "precipitation_probability_max": [5, 60, 20],
}}


def _lookup(routes):
    http = FakeHttp(routes)
    registry = FactProviderRegistry(ALLOW)
    for p in (ForecastProvider(http), GeocodingProvider(http)):
        registry.register(p)
    return LookupService(registry, ALLOW), http


def test_provider_picks_the_requested_day_and_asks_for_that_many_days():
    http = FakeHttp({"v1/forecast": DAILY_OK})
    ans = run(ForecastProvider(http).fetch(FactQuery("forecast", {"lat": 1, "lon": 2, "date_offset": 1})))
    assert ans.data["date"] == "2026-10-08" and ans.data["condition"] == "rain"
    assert ans.data["temp_max_c"] == 24.4 and ans.data["rain_chance_pct"] == 60
    assert http.requests[0][2]["forecast_days"] == 2


@pytest.mark.parametrize("offset", [-1, 7, 9, "tomorrow", None])
def test_provider_rejects_a_bad_offset_without_a_request(offset):
    http = FakeHttp({})
    with pytest.raises(ValueError):
        run(ForecastProvider(http).fetch(FactQuery("forecast", {"lat": 1, "lon": 2, "date_offset": offset})))
    assert http.requests == []


def test_provider_reports_a_missing_day_as_unavailable():
    with pytest.raises(ToolUnavailableError):
        run(ForecastProvider(FakeHttp({"v1/forecast": {"daily": {"time": []}}})).fetch(
            FactQuery("forecast", {"lat": 1, "lon": 2, "date_offset": 1})))


def test_spoken_reply_names_the_place_and_the_day():
    lookup, _ = _lookup({"v1/search": GEO_OK, "v1/forecast": DAILY_OK})
    lk = WeatherLookup(lookup, PlaceResolver(lookup))
    tomorrow = run(lk.forecast("Hyderabad", 1)).spoken
    assert tomorrow == "In Hyderabad tomorrow, rain, 60 percent chance of rain, 19 to 24 degrees."
    assert run(lk.forecast("Hyderabad", 0)).spoken.startswith("In Hyderabad today,")
    assert run(lk.forecast("Hyderabad", 2)).spoken.startswith("In Hyderabad on Friday,")  # 2026-10-09 is a Friday


@pytest.mark.parametrize("offset", [-1, 7, 9, True, "1", None])
def test_out_of_range_offset_is_rejected_in_code_and_never_reaches_the_provider(offset):
    lookup, http = _lookup({"v1/search": GEO_OK, "v1/forecast": DAILY_OK})
    with pytest.raises(AppException):
        run(WeatherLookup(lookup, PlaceResolver(lookup)).forecast("Hyderabad", offset))
    assert http.requests == []


def test_skill_turns_a_bad_offset_into_plain_speech():
    lookup, http = _lookup({"v1/search": GEO_OK, "v1/forecast": DAILY_OK})
    skill = GetWeatherForecastSkill(WeatherLookup(lookup, PlaceResolver(lookup)), MANIFESTS["get_weather_forecast"])
    res = run(skill.execute(_ctx(), place="Hyderabad", date_offset=9))
    assert not res.success and "0 to 6 days" in res.metadata["spoken"] and http.requests == []
    ok = run(skill.execute(_ctx(), place="Hyderabad", date_offset=1))
    assert ok.success and "tomorrow" in ok.metadata["spoken"]
