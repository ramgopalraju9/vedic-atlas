"""Check one batch of samples before it is merged: the quick feedback loop for whoever (or whatever) writes them.

    python -m training.check training/datasets/gen/g01_weather.jsonl
    python -m training.check FILE --leak        # also compare meaning to the evaluation sets with the local embedding model (slower)

Prints every error with the sample id, then a summary. Exit code 0 = nothing blocks the batch (errors, copies of evaluation cases, repeated
ids and repeated sentences all block it); warnings are advice. A batch that exits 0 is guaranteed to survive `training.build`.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from training.build import dup_key, eval_utterances, kind, leaked, norm  # noqa: E402
from training.sample import Sample  # noqa: E402
from training.validate import validate  # noqa: E402


def load_lenient(path: Path) -> tuple[list[Sample], list[str]]:
    samples, problems = [], []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            samples.append(Sample(**json.loads(line)))
        except Exception as e:   # noqa: BLE001
            problems.append(f"line {n}: {type(e).__name__}: {str(e)[:120]}")
    return samples, problems


def looks_spoken(text: str) -> bool:
    """Lower case with no sentence punctuation: what speech-to-text hands over."""
    return text == text.lower() and not re.search(r"[.!?;:\"]", text)


def check(path: Path, with_embeddings: bool) -> int:
    samples, problems = load_lenient(path)
    blocking = list(problems)
    warnings = collections.Counter()
    ids = collections.Counter(s.id for s in samples)
    blocking += [f"{i}: id used {n} times" for i, n in ids.items() if n > 1]
    for s in samples:
        for issue in validate(s):
            if issue.level == "error":
                blocking.append(f"{s.id} [{issue.code}] {issue.message}  <- {s.user!r}")
            else:
                warnings[issue.code] += 1
    seen: dict[str, str] = {}
    for s in samples:
        k = dup_key(s)
        if k in seen:
            blocking.append(f"{s.id}: same sentence and context as {seen[k]}  <- {s.user!r}")
        seen.setdefault(k, s.id)
    copies = asyncio.run(leaked(samples, eval_utterances(), 0.97 if with_embeddings else 0.0))
    blocking += [f"{i}: copy of evaluation case {e!r}" for i, e in copies.items()]

    for line in blocking[:60]:
        print("ERROR", line)
    if len(blocking) > 60:
        print(f"... and {len(blocking) - 60} more")
    n = len(samples) or 1
    kinds = collections.Counter(kind(s) for s in samples)
    tools = collections.Counter(c["tool"] for s in samples for c in s.label.get("calls", []))
    print(f"\n{len(samples)} samples | kinds {dict(kinds)} | tools {dict(tools)}")
    print(f"categories {dict(collections.Counter(s.category for s in samples))}")
    print(f"with ACTIVE/RECENT: {sum(bool(s.active or s.history) for s in samples) * 100 // n}% | speech-style sentences: "
          f"{sum(looks_spoken(s.user) for s in samples) * 100 // n}% | groups {len({s.group for s in samples})} | warnings {dict(warnings)}")
    print("OK" if not blocking else f"{len(blocking)} PROBLEM(S) TO FIX")
    return 1 if blocking else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=Path)
    ap.add_argument("--leak", action="store_true", help="also compare meaning to the evaluation sets (embedding model)")
    args = ap.parse_args()
    sys.exit(check(args.file, args.leak))
