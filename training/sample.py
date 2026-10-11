"""One training example for the router (the control decode), and reading/writing them as JSON lines.

A sample is everything the router is shown for one turn plus the decision it should make:
  user      what was said (as speech-to-text would give it)
  active    the "ACTIVE: ..." line, or "" when nothing is active (exactly as session_state_policy renders it)
  history   up to two earlier exchanges [{"user": ..., "veda": ...}] (the router sees the last two, clipped to 200 chars)
  today     the clock the prompt shows, ISO "2026-10-09T18:30"
  label     the decision: {"needs_live_data": bool, "calls": [{"tool", "args"}], "clarification"?: str}
  tools     None = every tool (what is served today); a list = only those, to teach the router to read tool descriptions
  group     paraphrases of one idea share a group; splits and the leak check work per group
  status    "final", or "provisional" while the labeling guide leaves the behaviour undecided
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Sample:
    id: str
    category: str
    user: str
    label: dict
    active: str = ""
    history: list[dict] = field(default_factory=list)
    today: str = "2026-10-09T18:30"
    tools: list[str] | None = None
    source: str = "synthetic"     # synthetic | seed
    group: str = ""
    status: str = "final"         # final | provisional
    notes: str = ""

    def __post_init__(self) -> None:
        self.group = self.group or self.id


def load(path: str | Path) -> list[Sample]:
    samples = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            samples.append(Sample(**json.loads(line)))
        except (TypeError, ValueError) as e:
            raise ValueError(f"{path}:{n}: {e}") from e
    return samples


def dump(samples: list[Sample], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        "".join(json.dumps(asdict(s), ensure_ascii=False) + "\n" for s in samples), encoding="utf-8", newline="\n",
    )
