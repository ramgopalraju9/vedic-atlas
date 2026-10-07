"""AmbientDispatcher: the ambient-event path extracted from SupervisorAgent (guide Phase 6, first step)."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from controller.routes import config as config_routes
from domain.events.ambient_event import AmbientEvent
from domain.events.event_kind import EventKind
from domain.value_objects.urgency import Urgency
from service.sensing.ambient_dispatcher import AmbientDispatcher


def run(d, **kw):
    return asyncio.run(d.dispatch_ambient(AmbientEvent(**kw)))


def test_heartbeats_are_never_voiced():
    assert run(AmbientDispatcher(proactivity="chatty"), kind=EventKind.HEARTBEAT, description="tick") is None


def test_low_urgency_is_voiced_only_when_chatty():
    ev = dict(kind=EventKind.NOTIFICATION, description="hello", urgency=Urgency.LOW)
    assert run(AmbientDispatcher(proactivity="medium"), **ev) is None
    assert run(AmbientDispatcher(proactivity="chatty"), **ev) == "hello"


def test_conservative_drops_normal_observations_but_keeps_notifications():
    d = AmbientDispatcher(proactivity="conservative")
    assert run(d, kind=EventKind.OBSERVATION, description="saw a thing", urgency=Urgency.NORMAL) is None
    assert run(d, kind=EventKind.NOTIFICATION, description="ping", urgency=Urgency.NORMAL, dedupe_key="n") == "ping"


def test_same_dedupe_key_inside_the_window_is_suppressed():
    d = AmbientDispatcher()
    ev = dict(kind=EventKind.NOTIFICATION, description="door open", dedupe_key="door")
    assert run(d, **ev) == "door open"
    assert run(d, **ev) is None


def test_rate_limit_applies_except_to_high_urgency():
    d = AmbientDispatcher(rate_limit_max=2)
    voiced = [run(d, kind=EventKind.NOTIFICATION, description=f"n{i}", dedupe_key=f"k{i}") for i in range(4)]
    assert voiced == ["n0", "n1", None, None]
    assert run(d, kind=EventKind.NOTIFICATION, description="fire", urgency=Urgency.HIGH, dedupe_key="fire") == "fire"


def test_chatty_doubles_the_rate_ceiling():
    d = AmbientDispatcher(rate_limit_max=2, proactivity="medium")
    d.set_proactivity("chatty")
    voiced = [run(d, kind=EventKind.NOTIFICATION, description=f"n{i}", dedupe_key=f"k{i}") for i in range(5)]
    assert voiced.count(None) == 1


def test_set_proactivity_validates():
    d = AmbientDispatcher()
    assert d.set_proactivity("chatty") == "chatty" and d.proactivity == "chatty"
    with pytest.raises(ValueError):
        d.set_proactivity("loud")
    assert AmbientDispatcher(proactivity="loud").proactivity == "medium"


def test_summary_notifications_are_mirrored_into_history_and_others_are_not():
    added = []
    conv = SimpleNamespace(add_turn=lambda role, text: added.append((role, text)))
    d = AmbientDispatcher(conversation=conv)
    run(d, kind=EventKind.NOTIFICATION, description="build finished", payload={"is_summary": True}, dedupe_key="a")
    run(d, kind=EventKind.NOTIFICATION, description="just a ping", dedupe_key="b")
    assert added == [("assistant", "build finished")]


def test_proactivity_routes_use_the_dispatcher_not_the_supervisor():
    app = FastAPI()
    app.include_router(config_routes.router, prefix="/api")
    app.state.ambient_dispatcher = AmbientDispatcher()
    c = TestClient(app)
    assert c.get("/api/config/proactivity").json()["level"] == "medium"
    assert c.post("/api/config/proactivity", json={"level": "chatty"}).json()["level"] == "chatty"
    assert app.state.ambient_dispatcher.proactivity == "chatty"
    assert c.post("/api/config/proactivity", json={"level": "loud"}).status_code == 400
