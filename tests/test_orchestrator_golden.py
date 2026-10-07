"""Structure of tests/eval/orchestrator_golden.yaml (docs/10, Phase 0).

Model-free: it only checks that the set is well formed and names real tools, so it
cannot rot silently. Measuring the model against it is scripts/eval_orchestrator.py.
"""

from pathlib import Path

import yaml

from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore

GOLDEN = yaml.safe_load((Path(__file__).parent / "eval" / "orchestrator_golden.yaml").read_text(encoding="utf-8"))
TOOLS = {m.name for m in YamlToolManifestStore().load_all()}
FUTURE_TOOLS = {"get_weather_forecast"}  # exist only after Phase 4, so only `phase4:` blocks may name them

REQUIRED_CATEGORIES = {
    "explicit", "followup", "mention", "negation", "quotation", "meta", "technical", "general",
    "no-tool", "no-state", "multi", "destructive", "slot-guard", "pending",
}


def _called_tools(expect):
    return [c["tool"] for c in expect.get("calls", [])]


def test_every_required_category_is_present():
    assert REQUIRED_CATEGORIES <= {g["category"] for g in GOLDEN}


def test_singapore_mention_case_is_present_and_first_of_its_kind():
    case = next(g for g in GOLDEN if g["category"] == "mention")
    assert case["say"] == "I didn't ask about Singapore weather, what's 1+1?"
    assert case["expect"] == {"calls": [], "needs_live_data": False}


def test_cases_are_well_formed_and_name_real_tools():
    for g in GOLDEN:
        assert g["say"].strip() and g["category"], g
        assert set(g["expect"]) <= {"calls", "needs_live_data", "clarification"}, g
        assert isinstance(g["expect"]["calls"], list), g
        assert set(_called_tools(g["expect"])) <= TOOLS, f"{g['say']!r} names a tool that does not exist yet"
        if "phase4" in g:
            assert set(_called_tools(g["phase4"])) <= TOOLS | FUTURE_TOOLS, g
        if g["expect"].get("clarification"):
            assert g["expect"]["calls"] == [], f"{g['say']!r}: a clarification case must not also call a tool"
        state = g.get("state")
        if state:
            assert state["tool"] in TOOLS, g
        for h in g.get("history", []):
            assert h["user"] and h["veda"], g


def test_followups_carry_the_state_they_depend_on():
    for g in (g for g in GOLDEN if g["category"] == "followup"):
        assert g.get("history") and g.get("state"), f"{g['say']!r} is a follow-up with nothing to follow"


def test_destructive_ambiguity_never_expects_a_call_unless_target_is_named():
    for g in (g for g in GOLDEN if g["category"] == "destructive"):
        if g["expect"].get("clarification"):
            assert g["expect"]["calls"] == []
        else:
            assert g["expect"]["calls"][0]["args"].get("title"), "a destructive call must name its target"


# ---- held-out set (never tune against it) -------------------------------------------------------------

HELDOUT = yaml.safe_load((Path(__file__).parent / "eval" / "orchestrator_heldout.yaml").read_text(encoding="utf-8"))


def test_heldout_is_well_formed_and_names_real_tools():
    assert len(HELDOUT) >= 40
    for g in HELDOUT:
        assert g["say"].strip() and g["category"], g
        assert set(g["expect"]) <= {"calls", "needs_live_data", "clarification"}, g
        assert set(_called_tools(g["expect"])) <= TOOLS, g
        if "phase4" in g:
            assert set(_called_tools(g["phase4"])) <= TOOLS | FUTURE_TOOLS, g
        if g["expect"].get("clarification"):
            assert g["expect"]["calls"] == [], g
        if g["category"] == "followup":
            assert g.get("history") and g.get("state"), g
        if g.get("state"):
            assert g["state"]["tool"] in TOOLS, g


def test_heldout_shares_no_utterance_with_the_tuned_golden_set():
    tuned = {g["say"].strip().lower().rstrip("?.!") for g in GOLDEN}
    clash = [g["say"] for g in HELDOUT if g["say"].strip().lower().rstrip("?.!") in tuned]
    assert not clash, f"held-out cases duplicate tuned ones: {clash}"
