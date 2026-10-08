"""Phase 3: weather / currency / search tools, place resolution, caching, error mapping.

No network: providers get a scripted fake HTTP client. Run: pytest tests/test_lookup_tools.py
"""

import asyncio

import pytest

from core.enums import ExceptionCode
from domain.entities.agent_context import AgentContext
from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery
from domain.policies.coordinate_policy import parse_lat_lon
from domain.policies.currency_policy import normalize_currency, parse_amount
from domain.policies.weather_code_policy import describe_weather_code
from exceptions.exception import AppException, ToolUnavailableError
from service.lookup.currency_lookup import CurrencyLookup
from service.lookup.lookup_service import LookupService
from service.lookup.place_resolver import PlaceResolver
from service.lookup.registry import FactProviderRegistry
from service.lookup.search_lookup import SearchLookup
from service.lookup.ttl_cache import TtlCache
from service.lookup.weather_lookup import WeatherLookup
from service.skills.builtin.currency import ConvertCurrencySkill
from service.skills.builtin.weather import GetWeatherSkill
from service.skills.builtin.web_search import WebSearchSkill
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore
from tpa.online.providers.fx import FxProvider
from tpa.online.providers.geocode import GeocodingProvider
from tpa.online.providers.tavily import TavilyProvider
from tpa.online.providers.weather import WeatherProvider

MANIFESTS = {m.name: m for m in YamlToolManifestStore().load_all()}
ALLOW = frozenset({
    "api.open-meteo.com", "geocoding-api.open-meteo.com", "api.frankfurter.dev", "open.er-api.com", "api.tavily.com",
})


def run(coro):
    return asyncio.run(coro)


class FakeHttp:
    """Scripted stand-in for AllowListedHttpClient. Keys are URL substrings."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    async def _respond(self, url, record):
        self.requests.append(record)
        for needle, resp in self.routes.items():
            if needle in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected request {url}")

    async def get(self, url, *, category, params=None, headers=None):
        return await self._respond(url, ("GET", url, params, headers))

    async def post_json(self, url, *, category, json, headers=None):
        return await self._respond(url, ("POST", url, json, headers))


GEO_OK = {"results": [
    {"name": "Hyderabad", "admin1": "Telangana", "country": "India", "latitude": 17.38, "longitude": 78.45,
     "population": 6993262, "timezone": "Asia/Kolkata"},
    {"name": "Hyderabad", "admin1": "Sindh", "country": "Pakistan", "latitude": 25.39, "longitude": 68.37,
     "population": 1921275},
]}
WEATHER_OK = {"timezone": "Asia/Kolkata", "current": {
    "time": "2026-10-03T20:30", "temperature_2m": 29.0, "apparent_temperature": 30.7,
    "relative_humidity_2m": 50, "weather_code": 0, "wind_speed_10m": 6.4, "is_day": 0}}
FX_OK = {"amount": 250.0, "base": "USD", "date": "2026-10-02", "rates": {"INR": 24081.0}}
TAVILY_OK = {"answer": "ISRO plans the NVS-03 launch in October. It will carry a NavIC satellite. More details soon.", "results": [
    {"title": "Old story", "url": "https://isro.gov.in/y", "content": "details", "published_date": "Sun, 27 Sep 2026 18:00:00 GMT"},
    {"title": "ISRO news", "url": "https://www.the-hindu.com/x", "content": "NVS-03 launch " * 40, "published_date": "2026-10-02"},
]}
ER_OK = {"result": "success", "time_last_update_utc": "Sat, 03 Oct 2026 00:02:32 +0000", "rates": {"AED": 3.6725, "INR": 96.0}}


def build(routes, key="k", ttl=None):
    http = FakeHttp(routes)
    registry = FactProviderRegistry(ALLOW)
    search = TavilyProvider(http, lambda: key)
    for p in (WeatherProvider(http), GeocodingProvider(http), FxProvider(http), search):
        registry.register(p)
    lookup = LookupService(registry, ALLOW, cache=TtlCache(), ttl_by_category=ttl or {})
    return lookup, http


# ---- pure policies --------------------------------------------------------

def test_currency_words_and_symbols_normalise():
    assert [normalize_currency(x) for x in ["rupees", "$", "USD", "euros", "pounds", "Yen", "€", "xyz1", ""]] == \
           ["INR", "USD", "USD", "EUR", "GBP", "JPY", "EUR", None, None]
    assert normalize_currency("chf") == "CHF"  # unknown 3-letter passes through upper-cased


def test_amount_parsing_rejects_nonsense():
    assert parse_amount("1,000.50") == 1000.5 and parse_amount(5) == 5.0
    assert all(parse_amount(x) is None for x in [0, -3, "abc", None, float("nan"), 1e15])


def test_coordinates_parse_and_validate():
    assert parse_lat_lon("Hyderabad is at 17.385° N, 78.4867° E") == (17.385, 78.4867)
    assert parse_lat_lon("Sydney 33.87 S 151.21 E") == (-33.87, 151.21)
    assert parse_lat_lon("latitude: 17.385 and longitude: 78.4867") == (17.385, 78.4867)
    assert parse_lat_lon("coordinates 17.385, 78.4867 approx") == (17.385, 78.4867)
    assert parse_lat_lon("the answer is 42") is None
    assert parse_lat_lon("95.5, 200.5") is None          # out of range
    assert parse_lat_lon("0.00, 0.00") is None           # null island = failed parse


def test_weather_codes():
    assert describe_weather_code(0) == "clear sky" and describe_weather_code(95) == "a thunderstorm"
    assert describe_weather_code("x") == "unknown conditions"


def test_ttl_cache_expires_with_injected_clock():
    now = [100.0]
    cache = TtlCache(clock=lambda: now[0])
    cache.put("k", "v", 10)
    assert cache.get("k") == "v"
    now[0] = 111.0
    assert cache.get("k") is None


# ---- providers --------------------------------------------------------------

def test_weather_provider_structures_the_reading():
    http = FakeHttp({"v1/forecast": WEATHER_OK})
    ans = run(WeatherProvider(http).fetch(FactQuery("weather", {"lat": 17.3, "lon": 78.4})))
    assert ans.data["temperature_c"] == 29.0 and ans.data["condition"] == "clear sky"
    assert ans.data["as_of"] == "2026-10-03T20:30" and "Local time 20:30" in ans.text
    assert http.requests[0][2]["timezone"] == "auto"


def test_weather_provider_rejects_missing_coordinates_and_empty_reading():
    with pytest.raises(ValueError):
        run(WeatherProvider(FakeHttp({})).fetch(FactQuery("weather", {})))
    with pytest.raises(ToolUnavailableError):
        run(WeatherProvider(FakeHttp({"v1/forecast": {"current": {}}})).fetch(FactQuery("weather", {"lat": 1, "lon": 2})))


def test_fx_provider_uses_server_side_conversion():
    http = FakeHttp({"v1/latest": FX_OK})
    ans = run(FxProvider(http).fetch(FactQuery("fx", {"from": "usd", "to": "inr", "amount": 250})))
    assert ans.data["converted"] == 24081.0 and ans.data["rate"] == pytest.approx(96.324)
    assert http.requests[0][2] == {"base": "USD", "symbols": "INR", "amount": 250.0}


def test_tavily_without_key_never_touches_the_network():
    http = FakeHttp({})
    with pytest.raises(ToolUnavailableError) as err:
        run(TavilyProvider(http, lambda: "").fetch(FactQuery("search", {"query": "x"})))
    assert err.value.not_configured and http.requests == []


def test_tavily_parses_trims_and_sends_bearer_key():
    http = FakeHttp({"/search": TAVILY_OK})
    ans = run(TavilyProvider(http, lambda: "secret").fetch(FactQuery("search", {"query": "isro", "topic": "news"})))
    method, url, body, headers = http.requests[0]
    assert headers == {"Authorization": "Bearer secret"} and body["topic"] == "news" and body["time_range"] == "week"
    # dates are normalised to ISO; the provider's relevance order is kept
    assert [h["published"] for h in ans.data["hits"]] == ["2026-09-27", "2026-10-02"]
    assert ans.data["hits"][1]["domain"] == "the-hindu.com"
    assert len(ans.data["hits"][0]["snippet"]) <= 200 and "secret" not in ans.text


# ---- lookup service ----------------------------------------------------------

def test_lookup_service_caches_per_category_ttl():
    lookup, http = build({"v1/latest": FX_OK}, ttl={"fx": 60})
    q = FactQuery("fx", {"from": "USD", "to": "INR", "amount": 250})
    first, second = run(lookup.fetch(q)), run(lookup.fetch(q))
    assert not first.cached and second.cached and len(http.requests) == 1


def test_lookup_service_without_ttl_never_caches():
    lookup, http = build({"v1/latest": FX_OK})
    q = FactQuery("fx", {"from": "USD", "to": "INR"})
    run(lookup.fetch(q)), run(lookup.fetch(q))
    assert len(http.requests) == 2


# ---- place resolution -----------------------------------------------------------

def test_place_resolver_name_default_and_ambiguity():
    lookup, _ = build({"v1/search": GEO_OK})
    places = PlaceResolver(lookup, default_place="Hyderabad")
    named = run(places.resolve("Hyderabad"))
    assert named.label() == "Hyderabad, Telangana, India" and named.source == "geocoder"
    assert run(places.resolve("")).source == "default"
    assert run(places.resolve("here")).source == "default"


def test_place_resolver_uses_search_coordinates_only_when_valid():
    lookup, http = build({
        "v1/search": {"results": []},
        "/search": {"answer": "Zork Town is at 12.5° N, 45.25° E.", "results": []},
    })
    place = run(PlaceResolver(lookup).resolve("Zork Town"))
    assert (place.lat, place.lon, place.source) == (12.5, 45.25, "search")


def test_place_resolver_gives_up_cleanly():
    lookup, _ = build({"v1/search": {"results": []}, "/search": {"answer": "I do not know.", "results": []}})
    with pytest.raises(AppException) as err:
        run(PlaceResolver(lookup).resolve("Nowhereville"))
    assert err.value.code == ExceptionCode.NOT_FOUND and "Nowhereville" in err.value.message


# ---- the lookups ---------------------------------------------------------------

def test_weather_lookup_builds_spoken_and_text_from_provider_numbers():
    lookup, _ = build({"v1/search": GEO_OK, "v1/forecast": WEATHER_OK})
    obs = run(WeatherLookup(lookup, PlaceResolver(lookup)).get("Hyderabad"))
    assert "29 degrees" in obs.spoken and "clear sky" in obs.spoken and "Hyderabad" in obs.spoken
    assert "Hyderabad, Telangana, India" in obs.text and obs.as_of == "2026-10-03T20:30"


def test_weather_falls_back_to_search_and_labels_it_approximate():
    lookup, _ = build({
        "v1/search": GEO_OK,
        "v1/forecast": ToolUnavailableError("t", "weather", "down", status=503),
        "/search": {"answer": "Around 30C and sunny.", "results": []},
    })
    obs = run(WeatherLookup(lookup, PlaceResolver(lookup)).get("Hyderabad"))
    assert "approximate" in obs.text and "web search" in obs.spoken and obs.data["approximate"]


def test_weather_failure_surfaces_when_fallback_is_also_down():
    lookup, _ = build({
        "v1/search": GEO_OK,
        "v1/forecast": ToolUnavailableError("t", "weather", "down", status=503),
        "/search": ToolUnavailableError("t", "search", "down"),
    })
    with pytest.raises(ToolUnavailableError):
        run(WeatherLookup(lookup, PlaceResolver(lookup)).get("Hyderabad"))


def test_currency_lookup_normalises_and_reports_the_provider_figure():
    lookup, http = build({"v1/latest": FX_OK})
    obs = run(CurrencyLookup(lookup).convert("250", "dollars", "rupees"))
    assert http.requests[0][2]["base"] == "USD" and http.requests[0][2]["symbols"] == "INR"
    assert "24,081.00 INR" in obs.spoken and "2026-10-02" in obs.spoken


def test_currency_lookup_validation_and_unsupported_currency():
    lookup, _ = build({
        "api.frankfurter.dev": ToolUnavailableError("t", "fx", "nope", status=404),
        "open.er-api.com": {"result": "error", "error-type": "unsupported-code"},
    })
    cur = CurrencyLookup(lookup)
    for args in [(-5, "usd", "inr"), ("abc", "usd", "inr"), (10, "usd", "???")]:
        with pytest.raises(AppException) as err:
            run(cur.convert(*args))
        assert err.value.code == ExceptionCode.VALIDATION_ERROR
    with pytest.raises(AppException) as err:
        run(cur.convert(10, "usd", "xyz"))  # provider 404 -> not a currency we can price
    assert err.value.code == ExceptionCode.VALIDATION_ERROR
    assert "same currency" in run(cur.convert(5, "usd", "dollars")).spoken


def test_search_news_speaks_the_top_dated_headlines_as_final_text():
    lookup, _ = build({"/search": TAVILY_OK})
    obs = run(SearchLookup(lookup).search("isro news", "news"))
    assert obs.spoken == "Latest from the web: Old story (isro, 27 Sep); ISRO news (the hindu, 2 Oct)."
    assert obs.final and obs.text.startswith("WEB SEARCH for 'isro news'")


def test_search_general_speaks_the_first_sentences_of_the_answer():
    lookup, _ = build({"/search": TAVILY_OK})
    obs = run(SearchLookup(lookup).search("isro launch"))
    assert obs.spoken == "ISRO plans the NVS-03 launch in October. It will carry a NavIC satellite. More details soon. Source: isro."
    assert obs.final


def test_search_without_provider_answer_leaves_phrasing_to_the_narrate_stage():
    no_answer = {"answer": "", "results": TAVILY_OK["results"]}
    lookup, _ = build({"/search": no_answer})
    obs = run(SearchLookup(lookup).search("isro launch"))
    assert not obs.final and "Old story" in obs.spoken


def test_search_empty_result_and_blank_query():
    lookup, _ = build({"/search": TAVILY_OK})
    empty, _ = build({"/search": {"answer": "", "results": []}})
    with pytest.raises(AppException) as err:
        run(SearchLookup(empty).search("zzz"))
    assert err.value.code == ExceptionCode.NOT_FOUND
    with pytest.raises(AppException):
        run(SearchLookup(lookup).search("   "))


# ---- skills: the safety envelope -----------------------------------------------

def _ctx():
    return AgentContext(user_message="x")


def test_skill_maps_provider_outage_to_plain_speech_not_a_trace():
    lookup, _ = build({"v1/search": GEO_OK, "v1/forecast": ToolUnavailableError("t", "weather", "down", status=503)})
    skill = GetWeatherSkill(WeatherLookup(lookup, PlaceResolver(lookup)), MANIFESTS["get_weather"])
    res = run(skill.execute(_ctx(), place="Hyderabad"))
    assert not res.success and "didn't respond" in res.metadata["spoken"]


def test_skill_reports_missing_search_key_as_not_set_up():
    lookup, _ = build({}, key="")
    res = run(WebSearchSkill(SearchLookup(lookup), MANIFESTS["web_search"]).execute(_ctx(), query="isro"))
    assert not res.success and "isn't set up" in res.metadata["spoken"]


def test_skill_rejects_unknown_params_and_bad_values():
    lookup, _ = build({"v1/latest": FX_OK})
    skill = ConvertCurrencySkill(CurrencyLookup(lookup), MANIFESTS["convert_currency"])
    assert not run(skill.execute(_ctx(), amount=1, from_="USD", to="INR")).success  # unknown param
    bad = run(skill.execute(_ctx(), **{"amount": -1, "from": "USD", "to": "INR"}))
    assert not bad.success and "couldn't convert that" in bad.metadata["spoken"]
    ok = run(skill.execute(_ctx(), **{"amount": 250, "from": "USD", "to": "INR"}))
    assert ok.success and ok.metadata["source"] == "frankfurter"


# ---- manifests --------------------------------------------------------------------

def test_every_manifest_is_small_and_consistent():
    assert set(MANIFESTS) == {
        "tasks", "get_weather", "get_weather_forecast", "convert_currency", "web_search", "remember",
        "app_control", "volume_control", "device_status",
        "gmail_search", "gmail_read", "gmail_draft", "gmail_send", "calendar_agenda",
        "calendar_create", "current_time",
    }
    for m in MANIFESTS.values():
        assert 1 <= len(m.examples) <= 6
        if m.requires_online:
            assert m.hosts
            assert m.private or m.cache_ttl_sec > 0   # a public lookup is cached; the user's mail and calendar never are
    assert MANIFESTS["web_search"].reply_mode == "llm"
    assert MANIFESTS["get_weather"].reply_mode == MANIFESTS["convert_currency"].reply_mode == "template"


# ---- currency fallback (ECB first, wider-coverage second) -------------------------

def test_fx_falls_back_for_currencies_the_primary_does_not_carry():
    http = FakeHttp({
        "api.frankfurter.dev": ToolUnavailableError("t", "fx", "not found", status=404),
        "open.er-api.com": ER_OK,
    })
    ans = run(FxProvider(http).fetch(FactQuery("fx", {"from": "USD", "to": "AED", "amount": 200})))
    assert ans.provider_id.startswith("exchangerate-api") and ans.sources == ("open.er-api.com",)
    assert ans.data["converted"] == pytest.approx(734.5) and ans.data["date"] == "2026-10-03"


def test_fx_falls_back_when_the_primary_is_down():
    http = FakeHttp({"api.frankfurter.dev": ToolUnavailableError("t", "fx", "down", status=503), "open.er-api.com": ER_OK})
    ans = run(FxProvider(http).fetch(FactQuery("fx", {"from": "USD", "to": "INR", "amount": 1})))
    assert ans.data["rate"] == pytest.approx(96.0)


def test_fx_reports_unsupported_currency_not_an_outage():
    http = FakeHttp({
        "api.frankfurter.dev": ToolUnavailableError("t", "fx", "not found", status=404),
        "open.er-api.com": {"result": "error", "error-type": "unsupported-code"},
    })
    with pytest.raises(ToolUnavailableError) as err:
        run(FxProvider(http).fetch(FactQuery("fx", {"from": "USD", "to": "ZZZ"})))
    assert err.value.status == 404


def test_fx_outage_everywhere_is_reported_as_an_outage():
    http = FakeHttp({
        "api.frankfurter.dev": ToolUnavailableError("t", "fx", "down", status=503),
        "open.er-api.com": ToolUnavailableError("t", "fx", "down", status=503),
    })
    with pytest.raises(ToolUnavailableError) as err:
        run(FxProvider(http).fetch(FactQuery("fx", {"from": "USD", "to": "INR"})))
    assert err.value.status == 503


def test_outlet_names_are_readable_aloud():
    from service.lookup.search_lookup import _outlet

    assert [_outlet(d) for d in ["en.wikipedia.org", "isro.gov.in", "bbc.co.uk", "business-standard.com", "the-hindu.com", ""]] == [
        "wikipedia", "isro", "bbc", "business standard", "the hindu", "the web",
    ]


# ---- news-topic gating and spoken clipping ---------------------------------------

@pytest.mark.parametrize("question,sent_topic", [
    ("what are the upcoming movies releasing in october from tollywood", "general"),
    ("who is the prime minister of japan", "general"),
    ("what's the latest news on isro", "news"),
    ("any headlines today", "news"),
])
def test_news_topic_is_only_used_when_the_question_sounds_like_news(question, sent_topic):
    lookup, http = build({"/search": TAVILY_OK})
    skill = WebSearchSkill(SearchLookup(lookup), MANIFESTS["web_search"])
    res = run(skill.execute(AgentContext(user_message=question), query="x", topic="news"))  # the model always asked for news
    assert res.success and http.requests[0][2]["topic"] == sent_topic


def test_long_answers_are_clipped_at_a_list_boundary_not_mid_title():
    from service.lookup.search_lookup import _first_sentences

    movies = ", ".join(f'"Film Number {i}" on October {i}' for i in range(1, 30))
    spoken = _first_sentences(f"Upcoming releases include {movies}.")
    assert len(spoken) <= 340 and spoken.endswith(", and more.")
    assert spoken.count('"') % 2 == 0  # never cut inside a quoted title
    short = "Two short sentences. Fit fine."
    assert _first_sentences(short) == short
