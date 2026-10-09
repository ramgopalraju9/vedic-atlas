"""Turn a Sample into the exact text the router is trained on: the SAME bytes the server sends to the model at serving time.

Serving builds `system` + `volatile` with PromptComposer.control_stage and the llama.cpp client wraps them as one system
message and one user message with "/no_think" on its own line; the GGUF chat template then renders (checked for the 4B,
1.7B and 0.6B files, they render identically):

    <|im_start|>system\\n{system}<|im_end|>\\n<|im_start|>user\\n{volatile}\\n/no_think<|im_end|>\\n<|im_start|>assistant\\n

and the grammar makes the answer start with "{". So the training example is that prompt followed by the compact JSON decision
and <|im_end|>. HF `apply_chat_template` must NOT be used for the target: it would insert an empty <think> block before the
answer, which the grammar never allows at serving time.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from domain.entities.conversation import Turn  # noqa: E402
from domain.entities.tool_manifest import ToolManifest  # noqa: E402
from domain.policies.token_budget_policy import PromptBudgets  # noqa: E402
from domain.policies.tool_call_schema import CONTROL_KEYS  # noqa: E402
from service.prompting.prompt_composer import PromptComposer  # noqa: E402
from tpa.filestore.file_prompt_store import FilePromptStore  # noqa: E402
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore  # noqa: E402

from training.sample import Sample  # noqa: E402

CONTROL_BUDGET = 2800   # the qwen3-4b / 1.7b / 0.6b profiles' control budget (n_ctx 3072)
IM_END = "<|im_end|>"


@dataclass(frozen=True)
class Rendered:
    system: str
    volatile: str
    prompt: str        # what the model reads, ending with "<|im_start|>assistant\n"
    completion: str    # the decision JSON followed by <|im_end|>
    label_json: str


_MANIFESTS: dict[str, ToolManifest] | None = None


def manifests() -> dict[str, ToolManifest]:
    global _MANIFESTS
    if _MANIFESTS is None:
        _MANIFESTS = {m.name: m for m in YamlToolManifestStore().load_all()}
    return _MANIFESTS


def canonical_label(label: dict, tools: dict[str, ToolManifest] | None = None) -> str:
    """Compact JSON in the grammar's order: keys as CONTROL_KEYS, each call's args in the manifest's parameter order."""
    tools = tools or manifests()
    calls = []
    for call in label.get("calls") or []:
        order = [p.name for p in tools[call["tool"]].params]
        args = call.get("args") or {}
        calls.append({"tool": call["tool"], "args": {k: args[k] for k in order if k in args and args[k] is not None}})
    data: dict = {"needs_live_data": bool(label["needs_live_data"]), "calls": calls}
    clarification = label.get("clarification")
    if clarification:
        data["clarification"] = clarification
    ordered = {k: data[k] for k in CONTROL_KEYS if k in data}
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


def visible_tools(sample: Sample) -> dict[str, ToolManifest]:
    everything = manifests()
    if sample.tools is None:
        return everything
    return {name: everything[name] for name in sample.tools}


def composer_for(sample: Sample) -> PromptComposer:
    when = datetime.fromisoformat(sample.today)
    return PromptComposer(
        FilePromptStore(), list(visible_tools(sample).values()), budgets=PromptBudgets(control=CONTROL_BUDGET), now=lambda: when,
    )


def render(sample: Sample) -> Rendered:
    history = []
    base = datetime.fromisoformat(sample.today)
    for h in sample.history:   # the router is shown completed exchanges as User/Veda lines
        history.append(Turn(id=None, session_id="s", role="user", content=h["user"], created_at=base))
        history.append(Turn(id=None, session_id="s", role="assistant", content=h["veda"], created_at=base))
    composed = composer_for(sample).control_stage(
        sample.user, history, sample.active, exchanges=2, key_order=CONTROL_KEYS, show_empty=True,
    )
    label_json = canonical_label(sample.label, visible_tools(sample))
    prompt = (
        f"<|im_start|>system\n{composed.system}{IM_END}\n<|im_start|>user\n{composed.prompt}\n/no_think{IM_END}\n<|im_start|>assistant\n"
    )
    return Rendered(composed.system, composed.prompt, prompt, label_json + IM_END, label_json)
