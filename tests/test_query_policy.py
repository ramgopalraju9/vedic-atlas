"""QueryPolicy (relative dates, relevance gate) and its use by SearchLookup. No network."""

import asyncio
from datetime import date

import pytest

from core.enums import ExceptionCode
from domain.policies.query_policy import distinctive_terms, resolve_relative_dates, results_look_relevant
from exceptions.exception import AppException
from service.lookup.lookup_service import LookupService
from service.lookup.registry import FactProviderRegistry
from service.lookup.search_lookup import SearchLookup
from tpa.online.providers.tavily import TavilyProvider

SAT = date(2026, 10, 3)  # a Saturday


@pytest.mark.parametrize("query,expected", [
    ("upcoming Tollywood movies this month", "upcoming Tollywood movies October 2026"),
    ("releases next month", "releases November 2026"),
    ("what was released last month", "what was released September 2026"),
    ("movies this year", "movies 2026"),
    ("news today", "news 3 October 2026"),
    ("matches tomorrow", "matches 4 October 2026"),
    ("results yesterday", "results 2 October 2026"),
    ("films this week", "films week of 28 September 2026"),
    ("films next week", "films week of 5 October 2026"),
    ("shows this weekend", "shows weekend of 3 October 2026"),
    ("THIS MONTH releases", "October 2026 releases"),
    ("ISRO launch schedule", "ISRO launch schedule"),  # nothing relative: untouched
])
def test_relative_dates_become_concrete(query, expected):
    assert resolve_relative_dates(query, SAT) == expected


def test_month_arithmetic_rolls_over_the_year():
    assert resolve_relative_dates("next month", date(2026, 12, 15)) == "January 2027"
    assert resolve_relative_dates("last month", date(2026, 1, 2)) == "December 2025"


def test_distinctive_terms_ignore_generic_words_and_group_regional_names():
    assert distinctive_terms("upcoming movies releasing October 2026") == []
    assert distinctive_terms("upcoming Tollywood movies") == [frozenset({"tollywood", "telugu"})]
    assert distinctive_terms("ISRO latest news") == [frozenset({"isro"})]


def test_relevance_gate_catches_a_wrong_topic_answer():
    hollywood = ["Resident Evil release date and cast", "Avengers Endgame Encore returns to theatres next week"]
    telugu = ["Upcoming Telugu Movies October 2026 | Thella Kagitham, Sigma"]
    query = "upcoming Tollywood movies October 2026"
    assert not results_look_relevant(query, hollywood)
    assert results_look_relevant(query, telugu)           # "Telugu" satisfies "Tollywood"
    assert results_look_relevant("upcoming movies October 2026", hollywood)  # nothing distinctive -> can't judge


def test_half_of_the_terms_must_match():
    assert results_look_relevant("japan prime minister", ["Sanae Takaichi became prime minister of Japan"])
    assert not results_look_relevant("japan prime minister election", ["cricket scores and football fixtures"])


# ---- SearchLookup: date resolution, retry, honest failure -----------------------

class _Http:
    """Returns the scripted responses in order and records every request body."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.bodies = []

    async def post_json(self, url, *, category, json, headers=None):
        self.bodies.append(json)
        return self._responses.pop(0)


def _hit(title, content, url="https://www.filmibeat.com/x"):
    return {"title": title, "url": url, "content": content}


GOOD = {"answer": "Sigma and Thella Kagitham release on October 2.", "results": [
    _hit("Upcoming Telugu Movies October 2026", "Telugu films: Sigma, Thella Kagitham")]}
BAD = {"answer": "Resident Evil and Avengers Endgame: Encore release next week.", "results": [
    _hit("Resident Evil release date", "Hollywood horror franchise returns", "https://www.siasat.com/y")]}


def _lookup(responses):
    http = _Http(responses)
    allow = frozenset({"api.tavily.com"})
    registry = FactProviderRegistry(allow)
    registry.register(TavilyProvider(http, lambda: "key"))
    return SearchLookup(LookupService(registry, allow), today=lambda: SAT), http


def test_relative_date_is_resolved_before_the_query_leaves_the_device():
    search, http = _lookup([GOOD])
    obs = asyncio.run(search.search("upcoming Tollywood movies this month"))
    assert http.bodies[0]["query"] == "upcoming Tollywood movies October 2026"
    assert "Sigma" in obs.spoken


def test_off_topic_first_result_triggers_one_advanced_retry():
    search, http = _lookup([BAD, GOOD])
    obs = asyncio.run(search.search("upcoming Tollywood movies this month"))
    assert [b["search_depth"] for b in http.bodies] == ["basic", "advanced"]
    assert "Sigma" in obs.spoken and "Resident Evil" not in obs.spoken


def test_still_off_topic_after_retry_refuses_instead_of_reporting_it():
    search, http = _lookup([BAD, BAD])
    with pytest.raises(AppException) as err:
        asyncio.run(search.search("upcoming Tollywood movies this month"))
    assert err.value.code == ExceptionCode.NOT_FOUND and "reliable results" in err.value.message
    assert len(http.bodies) == 2


def test_on_topic_first_result_costs_a_single_request():
    search, http = _lookup([GOOD])
    asyncio.run(search.search("Tollywood movies October 2026"))
    assert len(http.bodies) == 1 and http.bodies[0]["search_depth"] == "basic"


def test_match_text_is_never_exposed_to_the_reply():
    search, _ = _lookup([GOOD])
    obs = asyncio.run(search.search("Tollywood movies"))
    assert all("match_text" not in h for h in obs.data["hits"])
