"""Assemble the training set: validate, de-duplicate, keep the evaluation sets out, render, split, report.

    python -m training.build --in training/datasets/pilot.jsonl --out training/datasets/build_pilot

Order of work, each step reported with counts:
  1. every sample is validated (training/validate.py); errors are dropped, warnings are counted and sampled
  2. exact duplicates (same words, same context) are dropped
  3. anything equal or very close in meaning to a case in the evaluation sets is dropped, so the test stays a test
     (normalised text, then cosine >= --leak with the local bge-small model)
  4. each sample is rendered into the exact serving text (training/render.py) and counted with the model's own tokenizer
  5. a deterministic split by `group` (paraphrases never straddle train and validation)
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from training import sample as sample_io  # noqa: E402
from training.augment import augment  # noqa: E402
from training.render import render  # noqa: E402
from training.sample import Sample  # noqa: E402
from training.validate import validate  # noqa: E402

EVAL_FILES = [ROOT / "tests" / "eval" / f"orchestrator_{n}.yaml" for n in ("golden", "heldout", "edge")]
TOKENIZER_GGUF = ROOT / "data" / "Qwen3-4B-Q4_K_M.gguf"      # every Qwen3 size shares this tokenizer
_NORM = re.compile(r"[^a-z0-9 ]")


def norm(text: str) -> str:
    return " ".join(_NORM.sub("", (text or "").lower()).split())


def dup_key(s: Sample) -> str:
    return norm(s.user) + "|" + s.active + "|" + norm(" ".join(h["user"] for h in s.history)) + "|" + ",".join(s.tools or [])


def kind(s: Sample) -> str:
    if s.label.get("calls"):
        return "call"
    if s.label.get("clarification"):
        return "clarify"
    return "refuse" if s.label.get("needs_live_data") else "chat"


def eval_utterances(files=EVAL_FILES) -> list[str]:
    out = []
    for f in files:
        if Path(f).exists():
            out += [c["say"] for c in yaml.safe_load(Path(f).read_text(encoding="utf-8")) or []]
    return out


async def leaked(samples: list[Sample], evals: list[str], threshold: float) -> dict[str, str]:
    """sample id -> the evaluation sentence it equals or nearly equals."""
    by_norm = {norm(e): e for e in evals}
    found = {s.id: by_norm[norm(s.user)] for s in samples if norm(s.user) in by_norm}
    model_dir = ROOT / "data" / "bge-small-en-v1.5"
    if threshold and (model_dir / "tokenizer.json").exists():
        from tpa.inference.embedding_adapter import OnnxEmbeddingProvider

        emb = OnnxEmbeddingProvider(model_dir)
        eval_vecs = [(e, (await emb.embed(e)).vector) for e in evals]
        for s in samples:
            if s.id in found:
                continue
            v = (await emb.embed(s.user)).vector
            best = max(((sum(a * b for a, b in zip(v, ev)), e) for e, ev in eval_vecs), key=lambda p: p[0])
            if best[0] >= threshold:
                found[s.id] = f"{best[1]} (cos {best[0]:.2f})"
    return found


def token_counter():
    if not TOKENIZER_GGUF.exists():
        return lambda text: len(text) // 4
    from llama_cpp import Llama

    llm = Llama(model_path=str(TOKENIZER_GGUF), vocab_only=True, verbose=False)
    return lambda text: len(llm.tokenize(text.encode("utf-8"), add_bos=False, special=True))


def cap_tools(samples: list[Sample], caps: dict[str, int]) -> tuple[list[Sample], dict[str, int]]:
    """Keep at most N single-tool call samples per capped tool, dropping whole paraphrase groups (hash order, so it is deterministic).
    Multi-call samples and everything else are never dropped here."""
    dropped: dict[str, int] = {}
    keep_ids: set[str] = set()
    for tool, limit in caps.items():
        mine = [s for s in samples if [c["tool"] for c in s.label.get("calls", [])] == [tool]]
        if len(mine) <= limit:
            continue
        groups: dict[str, list[Sample]] = {}
        for s in mine:
            groups.setdefault(s.group, []).append(s)
        kept = 0
        for g in sorted(groups, key=lambda g: hashlib.sha1(g.encode()).hexdigest()):
            if kept + len(groups[g]) <= limit:
                kept += len(groups[g])
                keep_ids.update(s.id for s in groups[g])
        drop = {s.id for s in mine} - keep_ids
        dropped[tool] = len(drop)
        samples = [s for s in samples if s.id not in drop]
    return samples, dropped


def split_of(group: str, val_percent: int) -> str:
    return "val" if int(hashlib.sha1(group.encode()).hexdigest(), 16) % 100 < val_percent else "train"


def build(
    paths: list[Path], out: Path, *, exclude_provisional: bool, val_percent: int, leak: float, evals: list[str], subset_probability: float = 0.0,
    caps: dict[str, int] | None = None,
) -> dict:
    samples: list[Sample] = []
    for p in paths:
        samples += sample_io.load(p)
    report: dict = {"loaded": len(samples)}

    if exclude_provisional:
        report["provisional_excluded"] = sum(s.status == "provisional" for s in samples)
        samples = [s for s in samples if s.status != "provisional"]

    ids = collections.Counter(s.id for s in samples)
    report["duplicate_ids"] = [i for i, n in ids.items() if n > 1]

    kept, errors, warns = [], collections.defaultdict(list), collections.Counter()
    for s in samples:
        issues = validate(s)
        bad = [i for i in issues if i.level == "error"]
        for i in issues:
            if i.level == "warn":
                warns[i.code] += 1
        if bad or ids[s.id] > 1:
            for i in bad:
                errors[i.code].append(f"{s.id}: {i.message}")
        else:
            kept.append(s)
    report["invalid"] = {code: len(v) for code, v in errors.items()}
    report["invalid_examples"] = {code: v[:3] for code, v in errors.items()}
    report["warnings"] = dict(warns)

    seen, unique = set(), []
    for s in kept:
        k = dup_key(s)
        if k not in seen:
            seen.add(k)
            unique.append(s)
    report["duplicates_dropped"] = len(kept) - len(unique)

    hits = asyncio.run(leaked(unique, evals, leak))
    report["leaked_into_eval_dropped"] = len(hits)
    report["leak_examples"] = [f"{i}: ~ {e}" for i, e in list(hits.items())[:5]]
    final = [s for s in unique if s.id not in hits]
    final, capped = cap_tools(final, caps or {})
    report["capped_dropped"] = capped
    variants = augment(final, subset_probability) if subset_probability else []
    report["tool_subset_variants_added"] = len(variants)
    final += variants

    count = token_counter()
    rows = {"train": [], "val": []}
    tokens = []
    for s in final:
        r = render(s)
        n = count(r.prompt) + count(r.completion)
        tokens.append((count(r.prompt), count(r.completion)))
        rows[split_of(s.group, val_percent)].append(
            {"id": s.id, "category": s.category, "group": s.group, "kind": kind(s), "prompt": r.prompt, "completion": r.completion, "tokens": n}
        )
    out.mkdir(parents=True, exist_ok=True)
    for name, lines in rows.items():
        (out / f"{name}.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8", newline="\n")

    report["kept"] = len(final)
    report["train"], report["val"] = len(rows["train"]), len(rows["val"])
    report["by_kind"] = dict(collections.Counter(kind(s) for s in final))
    report["by_category"] = dict(collections.Counter(s.category for s in final))
    tool_use = collections.Counter(c["tool"] for s in final for c in s.label.get("calls", []))
    report["tools_used"] = dict(tool_use)
    if tokens:
        total = [p + c for p, c in tokens]
        report["tokens"] = {"prompt_mean": int(statistics.mean(p for p, _ in tokens)), "completion_mean": int(statistics.mean(c for _, c in tokens)),
                            "max_total": max(total), "sum_total": sum(total)}
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inputs", nargs="+", required=True, help="JSON-lines files of samples")
    ap.add_argument("--out", required=True)
    ap.add_argument("--exclude-provisional", action="store_true", help="leave out samples still marked provisional")
    ap.add_argument("--val-percent", type=int, default=10)
    ap.add_argument("--cap", nargs="*", default=[], metavar="TOOL=N", help="keep at most N single-tool samples of a tool, e.g. get_weather_forecast=220")
    ap.add_argument("--subset-prob", type=float, default=0.15, help="share of eligible samples that also get a variant with only some tools listed")
    ap.add_argument("--leak", type=float, default=0.97, help="cosine at which a sample counts as a copy of an evaluation case (0 = text match only)")
    args = ap.parse_args()
    report = build([Path(p) for p in args.inputs], Path(args.out), exclude_provisional=args.exclude_provisional,
                   val_percent=args.val_percent, leak=args.leak, evals=eval_utterances(), subset_probability=args.subset_prob,
                   caps={k: int(v) for k, v in (c.split('=') for c in args.cap)})
    for key, value in report.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
