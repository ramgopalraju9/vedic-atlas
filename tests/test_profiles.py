"""Every shipped model profile must be able to hold the control decode's prompt, or that profile cannot answer at all."""

from pathlib import Path

import pytest
import yaml

from domain.policies.token_budget_policy import PromptBudgets
from service.prompting.prompt_composer import PromptComposer
from tpa.filestore.file_prompt_store import FilePromptStore
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore

PROFILES = sorted((Path(__file__).resolve().parents[1] / "config" / "profiles").glob("*.yaml"))
DECISION_TOKENS = 200   # ControlDecoder.num_predict default


@pytest.mark.parametrize("path", PROFILES, ids=[p.stem for p in PROFILES])
def test_profiles_fit_the_control_prompt(path):
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    n_ctx = profile["inference"]["n_ctx"]
    control_budget = PromptBudgets(**profile.get("prompting", {}).get("budgets", {})).control
    composer = PromptComposer(FilePromptStore(), YamlToolManifestStore().load_all(), budgets=PromptBudgets(control=control_budget))
    prompt = composer.control_stage("x", show_empty=True)
    assert prompt.sections["static"] + 200 <= control_budget, "the static prompt leaves no room for history and the question"
    assert control_budget + DECISION_TOKENS <= n_ctx, f"{path.stem}: n_ctx {n_ctx} cannot hold a full control prompt + the decision"
    assert prompt.tokens + DECISION_TOKENS <= n_ctx
