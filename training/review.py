"""Second-labeler audit: a different labeler re-labels a random slice WITHOUT seeing the labels, and the two are compared.

    python -m training.review blind --in training/datasets/gen/*.jsonl --n 400    # writes review/blind.jsonl (no labels) and review/key.jsonl
    python -m training.review compare review/labels.jsonl                         # prints agreement and every disagreement

Disagreement means one of three things, all worth reading: the first label is wrong, the second is wrong, or the guide is ambiguous
(then fix the guide, not just the sample). Agreement is measured on the canonical JSON, so argument wording differences count.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from training import sample as sample_io  # noqa: E402
from training.build import kind  # noqa: E402
from training.render import canonical_label  # noqa: E402

REVIEW = ROOT / "training" / "datasets" / "review"


def blind(paths: list[Path], n: int, seed: int) -> None:
    samples = [s for p in paths for s in sample_io.load(p) if not s.id.endswith("-s") and s.status == "final"]
    by_category = collections.defaultdict(list)
    for s in samples:
        by_category[s.category].append(s)
    rng = random.Random(seed)
    chosen = []
    for cat, group in sorted(by_category.items()):   # proportional, at least 6 of every category
        take = min(len(group), max(6, round(n * len(group) / len(samples))))
        chosen += rng.sample(group, take)
    rng.shuffle(chosen)
    chosen = chosen[:n]
    REVIEW.mkdir(parents=True, exist_ok=True)
    with (REVIEW / "blind.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for s in chosen:
            f.write(json.dumps({"id": s.id, "user": s.user, "active": s.active, "history": s.history, "today": s.today}, ensure_ascii=False) + "\n")
    with (REVIEW / "key.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for s in chosen:
            f.write(json.dumps({"id": s.id, "category": s.category, "label": s.label, "user": s.user, "active": s.active}, ensure_ascii=False) + "\n")
    print(f"{len(chosen)} samples -> {REVIEW / 'blind.jsonl'} (labels withheld; key kept in key.jsonl)")


def compare(labels_path: Path) -> int:
    key = {r["id"]: r for r in (json.loads(l) for l in (REVIEW / "key.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}
    second = {}
    for line in labels_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            second[r["id"]] = r["label"]
    agree, rows, by_cat = 0, [], collections.defaultdict(lambda: [0, 0])
    for i, k in key.items():
        if i not in second:
            continue
        a, b = canonical_label(k["label"]), canonical_label(second[i])
        by_cat[k["category"]][1] += 1
        if a == b:
            agree += 1
            by_cat[k["category"]][0] += 1
        else:
            rows.append((k["category"], i, k["user"], k["active"], a, b))
    n = len(second)
    print(f"compared {n} of {len(key)}: exact agreement {agree}/{n} ({100 * agree // max(n, 1)}%)")
    print("by category:", {c: f"{a}/{t}" for c, (a, t) in sorted(by_cat.items())})
    # agreement on the KIND of decision (call / chat / refuse / clarify) and on the tool, which matters more than argument wording
    from training.sample import Sample
    same_kind = sum(kind(Sample(id="x", category="c", user="u", label=key[i]["label"])) == kind(Sample(id="x", category="c", user="u", label=second[i]))
                    for i in second if i in key)
    same_tools = sum([c["tool"] for c in key[i]["label"].get("calls", [])] == [c["tool"] for c in second[i].get("calls", [])] for i in second if i in key)
    print(f"same kind of decision: {same_kind}/{n}; same tools called: {same_tools}/{n}")
    out = REVIEW / "disagreements.jsonl"
    out.write_text("".join(json.dumps({"category": c, "id": i, "user": u, "active": ac, "first": a, "second": b}, ensure_ascii=False) + "\n"
                           for c, i, u, ac, a, b in rows), encoding="utf-8", newline="\n")
    print(f"{len(rows)} disagreements written to {out}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("blind")
    b.add_argument("--in", dest="inputs", nargs="+", required=True, type=Path)
    b.add_argument("--n", type=int, default=400)
    b.add_argument("--seed", type=int, default=11)
    c = sub.add_parser("compare")
    c.add_argument("labels", type=Path)
    args = ap.parse_args()
    if args.cmd == "blind":
        blind(args.inputs, args.n, args.seed)
    else:
        sys.exit(compare(args.labels))
