"""Tool-subset variants: the same request shown with only some of the tools listed.

Why: the router reads the tool list in its prompt. If it only ever sees today's 15 tools in one fixed order it can memorise them, and a
16th tool tomorrow would need retraining. Training on a share of examples with a shuffled subset teaches it to read the descriptions.

Only variants whose correct answer cannot change are made:
  * a call sample keeps every tool it calls (plus random others);
  * a chat sample (greeting, knowledge, a mention, a question about the conversation) may lose any tool.
Clarify and refuse samples are left alone: "email priya" would be a refusal, not a question, if the mail tools were not listed.
"""

from __future__ import annotations

import copy
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from training.render import manifests  # noqa: E402
from training.sample import Sample  # noqa: E402

MIN_TOOLS = 5


def subset_variant(s: Sample, rng: random.Random) -> Sample | None:
    everything = list(manifests())
    called = {c["tool"] for c in s.label.get("calls", [])}
    if s.tools is not None:
        return None
    if called:
        pass
    elif s.label.get("clarification") or s.label.get("needs_live_data"):
        return None
    others = [t for t in everything if t not in called]
    low = max(MIN_TOOLS - len(called), 1)
    chosen = rng.sample(others, rng.randint(low, len(others) - 1))      # always leaves at least one tool out
    variant = copy.deepcopy(s)
    variant.tools = [t for t in everything if t in called or t in chosen]      # manifest order, as the server lists them
    variant.id = f"{s.id}-s"
    return variant


def augment(samples: list[Sample], probability: float, seed: int = 7) -> list[Sample]:
    """Return the new variants only (the originals are kept by the caller)."""
    rng = random.Random(seed)
    out = []
    for s in samples:
        if rng.random() < probability:
            v = subset_variant(s, rng)
            if v is not None:
                out.append(v)
    return out
