"""Boot the REAL composition root (server.bootstrap) with a fake model and drive turns through the supervisor.

Unit tests build the pieces by hand, so a wiring mistake in server.py (a missing skill registration, a dropped
argument) passes them all. This one catches it. It runs in a subprocess from a throwaway COPY of src/ and config/:
PROJECT_ROOT, and therefore data/ (database, saved facts, models), resolves to the temp folder, so the user's real
data/ is never read or written.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_SCRIPT = '''
import asyncio, json, sys
sys.path.insert(0, "src")
import server
from fastapi import FastAPI
from core.config import ensure_dirs
from core.constants import PROJECT_ROOT
from domain.entities.agent_context import AgentContext
from tpa.persistence.migrations import init_tables

assert "veda-smoke" in str(PROJECT_ROOT), f"not isolated: {PROJECT_ROOT}"


class FakeSystem:
    def __init__(self): self.opened = []
    def open_app(self, name): self.opened.append(name); return True, f"Opening {name}."
    def close_app(self, name): return True, f"Closed {name}."
    def focus_app(self, name): return True, f"Focused {name}."
    def battery_info(self): return {"has_battery": True, "percent": 80, "plugged_in": False, "secs_left": 3600}
    def get_volume(self): return 40
    def set_volume(self, level): return True, f"Volume set to {level}."
    def mute(self, on): return True
    def is_muted(self): return False
    def list_running(self, f=None): return []
    def top_processes(self, limit=5): return [{"name": "x", "cpu": 1}]


class FakeClient:
    def __init__(self): self.script = []
    async def complete(self, prompt="", system="", model=None, json_schema=None, **kw):
        if json_schema is not None and "needs_live_data" in json_schema.get("properties", {}):
            return json.dumps(self.script.pop(0))
        return "Chat answer."
    async def stream(self, prompt, system="", model=None, cancel_event=None, **kw):
        yield "Chat answer."


client, system = FakeClient(), FakeSystem()
server._build_inference = lambda cfg, gov, lock=None, thread_lock=None: (client, client)
server._build_system_control = lambda: system

ensure_dirs()
init_tables()
app = FastAPI()
server.bootstrap(app)
sup = app.state.supervisor
names = {s.name for s in app.state.skill_registry.list_all()}
assert {"app_control", "volume_control", "device_status", "get_weather", "get_weather_forecast", "convert_currency",
        "web_search", "tasks", "remember"} <= names, names
assert {a.name for a in app.state.agent_registry.list_all()} == {"responder", "supervisor"}
assert app.state.ambient_dispatcher.proactivity in ("conservative", "medium", "chatty")


def turn(text, decision):
    client.script.append(decision)
    return asyncio.run(sup.execute(AgentContext(user_message=text))).response


def call(tool, **args):
    return {"tool": tool, "args": args}


def d(calls=(), live=False, **extra):
    return {"needs_live_data": live, "calls": list(calls), **extra}


assert turn("open notepad", d([call("app_control", action="open", name="notepad")], True)) == "Opening notepad."
assert system.opened == ["notepad"]
assert turn("set volume to 30", d([call("volume_control", action="set", level=30)], True)) == "Volume set to 30."
assert turn("close notepad", d([call("app_control", action="close", name="notepad")], True)) == "Closed notepad."
assert turn("close it", d([call("app_control", action="close", name="it")], True)) == "Which one do you mean?"
assert turn("what is 1+1", d()) == "Chat answer."
assert turn("how many meetings do i have", d(live=True)) == "I can\\'t look that up right now."
assert turn("and tomorrow", d(clarification="Where?")) == "Where?"
print("SMOKE OK")
'''


def test_the_real_composition_root_boots_and_runs_every_kind_of_turn(tmp_path):
    work = tmp_path / "veda-smoke"
    ignore = shutil.ignore_patterns("__pycache__", "*.egg-info", "*.pyc")
    shutil.copytree(ROOT / "src", work / "src", ignore=ignore)
    shutil.copytree(ROOT / "config", work / "config", ignore=ignore)
    (work / "run_smoke.py").write_text(_SCRIPT, encoding="utf-8")

    done = subprocess.run(
        [sys.executable, "run_smoke.py"], cwd=work, capture_output=True, text=True, timeout=180,
        env={**os.environ, "PYTHONPATH": str(work / "src")},
    )
    assert "SMOKE OK" in done.stdout, f"stdout:\n{done.stdout[-1500:]}\nstderr:\n{done.stderr[-2500:]}"
    assert (work / "data" / "veda.db").exists()      # the app wrote into the temp copy, never the real data/
