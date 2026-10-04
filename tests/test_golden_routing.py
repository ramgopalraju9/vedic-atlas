"""The golden set's routing half: deterministic, no model, runs in CI.

Every utterance in tests/eval/golden_set.yaml must route to its expected agent.
The model-dependent half (tool choice and arguments) is scripts/eval_tools.py --model.
"""

from pathlib import Path

import pytest
import yaml

from domain.entities.agent_profile import AgentProfile
from domain.policies.routing_policy import match_agent, pick_agent
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore

GOLDEN = yaml.safe_load((Path(__file__).parent / "eval" / "golden_set.yaml").read_text(encoding="utf-8"))
MANIFESTS = YamlToolManifestStore().load_all()


def _profiles():
    triggers: dict[str, list[str]] = {}
    for m in MANIFESTS:
        triggers.setdefault(m.agent, []).extend(m.triggers)
    chat = AgentProfile(name="responder", description="General conversation, Q&A, brainstorming", model_alias="")
    return (chat,) + tuple(
        AgentProfile(name=a, description=a, model_alias="", triggers=tuple(t)) for a, t in triggers.items()
    )


def test_golden_set_is_well_formed():
    assert len(GOLDEN) >= 40
    tools = {m.name for m in MANIFESTS} | {None}
    for item in GOLDEN:
        assert item["tool"] in tools, item
        assert (item["tool"] is None) == (item["agent"] == "responder") or item["agent"] in {"tasks", "lookup", "system", "memory"}


RULE_ITEMS = [g for g in GOLDEN if not g.get("llm")]
LLM_ITEMS = [g for g in GOLDEN if g.get("llm")]


@pytest.mark.parametrize("item", RULE_ITEMS, ids=[g["say"][:48] for g in RULE_ITEMS])
def test_routes_to_expected_agent(item):
    assert pick_agent(item["say"], _profiles(), "responder") == item["agent"]


@pytest.mark.parametrize("item", LLM_ITEMS, ids=[g["say"][:48] for g in LLM_ITEMS])
def test_llm_cases_really_are_unmatched_by_the_rules(item):
    # If a rule starts matching one of these, it is no longer testing the LLM router (and the rule decides it).
    assert match_agent(item["say"], _profiles()) is None
