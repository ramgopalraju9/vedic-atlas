"""Freeze the clean test set: drop near-copies of training samples, write the eval YAML the spike harness reads.

    python -m training.make_testset --test training/datasets/test/test_v1.jsonl --train training/datasets/gen/*.jsonl --out tests/eval/orchestrator_clean.yaml

The exam keeps only structured arguments in `expect` (tool, enums, numbers, offsets, times, place, to/from); free-text values (titles, queries,
subjects, bodies) are worded differently by every honest labeler, so they are not scored. Tool order and the clarify/refuse/chat decision are.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from training import sample as sample_io  # noqa: E402
from training.build import kind, leaked  # noqa: E402
from training.validate import _FREE_TEXT  # noqa: E402

_ACTIVE = re.compile(r"ACTIVE:\s*(\w+)\s*\|\s*(.*?)\s*\|")


def state_of(active: str):
    m = _ACTIVE.match(active or "")
    if not m:
        return None
    slots = {}
    for pair in re.findall(r"(\w+)=(\S+)", m.group(2)):
        slots[pair[0]] = pair[1]
    return {"tool": m.group(1), "slots": slots}


def case_of(s) -> dict:
    case = {"say": s.user, "category": s.category}
    if s.history:
        case["history"] = s.history
    st = state_of(s.active)
    if st:
        case["state"] = st
    calls = []
    for c in s.label.get("calls", []):
        args = {k: v for k, v in c["args"].items() if (c["tool"], k) not in _FREE_TEXT and k != "body"}
        calls.append({"tool": c["tool"], "args": args})
    expect: dict = {"calls": calls}
    k = kind(s)
    if k == "clarify":
        expect["clarification"] = True
    elif k in ("chat", "refuse"):
        expect["needs_live_data"] = k == "refuse"
    case["expect"] = expect
    return case


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--test", required=True, type=Path)
    ap.add_argument("--train", nargs="+", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--leak", type=float, default=0.93)
    args = ap.parse_args()
    test = sample_io.load(args.test)
    train_users = [s.user for p in args.train for s in sample_io.load(p)]
    hits = asyncio.run(leaked(test, train_users, args.leak))
    for i, e in hits.items():
        print(f"dropped {i}: ~ {e}")
    kept = [s for s in test if s.id not in hits]
    cases = [case_of(s) for s in kept]
    header = ("# CLEAN TEST SET: written by an author who never saw the training data; near-copies of training sentences removed.\n"
              "# Never train on it, never tune prompts against it. Only structured arguments are scored (see training/make_testset.py).\n"
              "#   python scripts/spike_decision_protocol.py --variants A1 --golden tests/eval/orchestrator_clean.yaml\n")
    args.out.write_text(header + yaml.safe_dump(cases, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8", newline="\n")
    print(f"test {len(test)} -> kept {len(kept)} (leaked {len(hits)}) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
