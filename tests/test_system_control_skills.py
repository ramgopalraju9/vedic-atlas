"""Phase 3 prep (docs/implementation_guide.md §3.5): SystemAgent's actions as ordinary manifest skills."""

import asyncio
from types import SimpleNamespace

from domain.entities.agent_context import AgentContext
from domain.policies.destructive_policy import is_destructive
from schemas.tool_manifest_schema import ToolManifestSchema
from service.skills.builtin.system_control import AppControlSkill, DeviceStatusSkill, VolumeControlSkill

APP = ToolManifestSchema(**{
    "name": "app_control", "agent": "system", "description": "Open, close or switch to an application on the user's device.",
    "reply_mode": "template", "permission_level": "notify", "destructive_when": {"action": ["close"]},
    "params": {"action": {"type": "string", "required": True, "enum": ["open", "close", "focus"]},
               "name": {"type": "string", "required": True}},
}).to_domain()
VOL = ToolManifestSchema(**{
    "name": "volume_control", "agent": "system", "description": "Read or change the device volume, or mute and unmute.",
    "reply_mode": "template", "permission_level": "auto",
    "params": {"action": {"type": "string", "required": True, "enum": ["get", "set", "mute", "unmute"]},
               "level": {"type": "integer"}},
}).to_domain()
STATUS = ToolManifestSchema(**{
    "name": "device_status", "agent": "system", "description": "Report battery level or the busiest running applications.",
    "reply_mode": "template", "permission_level": "auto",
    "params": {"what": {"type": "string", "required": True, "enum": ["battery", "processes"]}},
}).to_domain()


class FakePort:
    def __init__(self, **over):
        self.calls, self.over = [], over

    def _rec(self, name, *a):
        self.calls.append((name, *a))
        return self.over.get(name)

    def open_app(self, n): self._rec("open_app", n); return self.over.get("open_app", (True, f"Opening {n}."))
    def close_app(self, n): self._rec("close_app", n); return self.over.get("close_app", (True, f"Closed {n}."))
    def focus_app(self, n): self._rec("focus_app", n); return self.over.get("focus_app", (True, f"Switched to {n}."))
    def battery_info(self): self._rec("battery_info"); return self.over.get("battery_info", {"has_battery": True, "percent": 80, "plugged_in": False, "secs_left": 7200})
    def get_volume(self): self._rec("get_volume"); return self.over.get("get_volume", 40)
    def is_muted(self): return self.over.get("is_muted", False)
    def set_volume(self, level): self._rec("set_volume", level); return self.over.get("set_volume", (True, f"Volume set to {level}."))
    def mute(self, on): self._rec("mute", on); return self.over.get("mute", True)
    def top_processes(self, limit=5): self._rec("top_processes", limit); return self.over.get("top_processes", [{"name": "chrome", "cpu": 12}, {"name": "code", "cpu": 7}])


def run(skill, **params):
    return asyncio.run(skill.execute(AgentContext(user_message="x"), **params))


# ---- app_control -------------------------------------------------------------

def test_open_close_focus_call_the_matching_port_method_and_speak_the_ports_message():
    port = FakePort()
    skill = AppControlSkill(port, APP)
    assert run(skill, action="open", name="spotify").metadata["spoken"] == "Opening spotify."
    assert run(skill, action="close", name="spotify").metadata["spoken"] == "Closed spotify."
    assert run(skill, action="focus", name="chrome").metadata["spoken"] == "Switched to chrome."
    assert [c[0] for c in port.calls] == ["open_app", "close_app", "focus_app"]


def test_a_port_failure_is_a_failed_result_with_the_ports_own_words():
    r = run(AppControlSkill(FakePort(open_app=(False, "I couldn't find that app.")), APP), action="open", name="nope")
    assert not r.success and r.metadata["spoken"] == "I couldn't find that app."


def test_missing_name_asks_and_never_touches_the_device():
    port = FakePort()
    r = run(AppControlSkill(port, APP), action="open", name="  ")
    assert not r.success and r.metadata["spoken"] == "Which app do you mean?" and port.calls == []


def test_unknown_parameters_are_rejected_before_the_port_is_called():
    port = FakePort()
    assert not run(AppControlSkill(port, APP), action="open", name="x", extra=1).success and port.calls == []


def test_governance_denial_blocks_the_action_and_keeps_its_reason():
    port = FakePort()
    gov = SimpleNamespace(check_action=lambda kind, payload: SimpleNamespace(allowed=False, reason="apps are locked"))
    r = run(AppControlSkill(port, APP, governance=gov), action="close", name="x")
    assert not r.success and "apps are locked" in r.metadata["spoken"] and port.calls == []


def test_governance_is_asked_with_the_same_shape_systemagent_used():
    seen = []
    gov = SimpleNamespace(check_action=lambda kind, payload: (seen.append((kind, payload)) or SimpleNamespace(allowed=True, reason="")))
    run(AppControlSkill(FakePort(), APP, governance=gov), action="open", name="vs code")
    assert seen == [("system_action", {"tool": "open_app", "target": "vs code"})]


def test_closing_an_app_is_destructive_opening_is_not():
    assert is_destructive(APP, {"action": "close", "name": "x"})
    assert not is_destructive(APP, {"action": "open", "name": "x"}) and not is_destructive(VOL, {"action": "set", "level": 5})


# ---- volume_control ----------------------------------------------------------

def test_volume_get_reports_the_number_and_muted_state():
    assert run(VolumeControlSkill(FakePort(), VOL), action="get").metadata["spoken"] == "Volume is at 40%."
    assert run(VolumeControlSkill(FakePort(is_muted=True), VOL), action="get").metadata["spoken"] == "Volume is at 40% (muted)."


def test_volume_get_when_unreadable_says_so():
    r = run(VolumeControlSkill(FakePort(get_volume=None), VOL), action="get")
    assert not r.success and r.metadata["spoken"] == "I couldn't read the volume right now."


def test_volume_set_validates_the_range_in_code_and_never_sends_a_bad_value():
    port = FakePort()
    skill = VolumeControlSkill(port, VOL)
    for bad in (101, -1, None):
        r = run(skill, action="set", **({} if bad is None else {"level": bad}))
        assert not r.success and "between 0 and 100" in r.metadata["spoken"]
    assert port.calls == []
    assert run(skill, action="set", level=30).metadata["spoken"] == "Volume set to 30." and port.calls == [("set_volume", 30)]
    assert run(skill, action="set", level=0).success and run(skill, action="set", level=100).success


def test_mute_and_unmute_confirm_from_the_boolean_the_port_returns():
    port = FakePort()
    skill = VolumeControlSkill(port, VOL)
    assert run(skill, action="mute").metadata["spoken"] == "Muted." and run(skill, action="unmute").metadata["spoken"] == "Unmuted."
    assert port.calls == [("mute", True), ("mute", False)]
    r = run(VolumeControlSkill(FakePort(mute=False), VOL), action="mute")
    assert not r.success and "couldn't change" in r.metadata["spoken"]          # never claims "Muted." when it failed


# ---- device_status -----------------------------------------------------------

def test_battery_on_battery_charging_and_absent():
    assert run(DeviceStatusSkill(FakePort(), STATUS), what="battery").metadata["spoken"] == "Battery is at 80%, on battery, about 120 minutes left."
    charging = {"has_battery": True, "percent": 55, "plugged_in": True, "secs_left": None}
    assert run(DeviceStatusSkill(FakePort(battery_info=charging), STATUS), what="battery").metadata["spoken"] == "Battery is at 55%, charging."
    none = {"has_battery": False}
    r = run(DeviceStatusSkill(FakePort(battery_info=none), STATUS), what="battery")
    assert r.success and r.metadata["spoken"] == "This device doesn't report a battery."


def test_processes_are_listed_and_capped_at_five():
    r = run(DeviceStatusSkill(FakePort(), STATUS), what="processes")
    assert r.metadata["spoken"] == "Busiest right now: chrome (12%), code (7%)."
    many = [{"name": f"p{i}", "cpu": i} for i in range(9)]
    spoken = run(DeviceStatusSkill(FakePort(top_processes=many), STATUS), what="processes").metadata["spoken"]
    assert spoken.count("%)") == 5
    assert run(DeviceStatusSkill(FakePort(top_processes=[]), STATUS), what="processes").metadata["spoken"] == "Nothing notable running."


def test_port_exceptions_become_a_failed_result_not_a_crash():
    class Boom(FakePort):
        def get_volume(self): raise OSError("audio device gone")
    r = run(VolumeControlSkill(Boom(), VOL), action="get")
    assert not r.success and "couldn't" in r.metadata["spoken"]
