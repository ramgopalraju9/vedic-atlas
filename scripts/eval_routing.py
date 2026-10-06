"""Evaluate message routing (which specialist agent gets a message) on the golden set, per routing mode.

    python scripts/eval_routing.py                      # all three modes: keyword, hybrid, model
    python scripts/eval_routing.py --mode model         # one mode
    python scripts/eval_routing.py --save --label try1  # also write tests/eval/results/routing-<label>-<time>.json

Modes (config/routing.yaml):
  keyword  the manifest trigger rules + word overlap, default chat on a miss. No model.
  model    the router model (routing.model.model_path, e.g. Qwen3-0.6B) decides every message.
  hybrid   rules decide when SURE (domain.policies.routing_policy.is_confident), the router model decides the rest.

Reports, per mode: accuracy; per-agent true/false positives and negatives with precision and recall; how many
chat messages were wrongly sent to a tool agent ("hijacks") and how many tool requests were lost to chat.
For hybrid it also prints the sure/confused table: was the rules' pick RIGHT or WRONG when the rules claimed to
be sure, and when they were not? (A "sure but wrong" is the dangerous cell: it skips the model and mis-routes.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import zlib

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.config import active_profile, load_full_config  # noqa: E402
from domain.entities.agent_profile import AgentProfile  # noqa: E402
from domain.policies.routing_policy import is_confident, match_agent_detail  # noqa: E402
from service.agent.llm_router import LlmRouter  # noqa: E402
from service.prompting.prompt_composer import PromptComposer  # noqa: E402
from tpa.filestore.file_prompt_store import FilePromptStore  # noqa: E402
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore  # noqa: E402

AGENTS = ("responder", "system", "lookup", "memory", "tasks")  # the routable agents, as registered in production


def split_of(sentence: str) -> str:
    """Stable dev/test half of a golden sentence. Tune prompts and examples against `dev`; quote `test` only at the end."""
    return "dev" if zlib.crc32(sentence.lower().encode("utf-8")) % 2 == 0 else "test"


def _profiles(cfg, manifests) -> tuple[AgentProfile, ...]:
    triggers: dict[str, list[str]] = {}
    for m in manifests:
        triggers.setdefault(m.agent, []).extend(m.triggers)
    return tuple(
        AgentProfile(name=a, description=getattr(cfg.agents, a).description, model_alias="", triggers=tuple(triggers.get(a, ())))
        for a in AGENTS
    )


def _build_router(cfg, manifests):
    from tpa.inference.llama_cpp_client import LlamaCppClient

    m = cfg.routing.model
    path = Path(m.model_path or cfg.inference.model_path)
    client = LlamaCppClient(
        path if path.is_absolute() else ROOT / path,
        n_ctx=m.n_ctx if m.model_path else cfg.inference.n_ctx, n_threads=m.n_threads,
        num_predict=m.num_predict, prompt_cache_mb=m.prompt_cache_mb,
    )
    composer = PromptComposer(FilePromptStore(), manifests, count_tokens=client.count_tokens)
    return LlmRouter(client, composer, num_predict=m.num_predict, timeout_sec=max(m.timeout_sec, 30.0)), path.name


def _confusion(records: list[dict]) -> dict[str, dict]:
    out = {}
    for a in AGENTS:
        tp = sum(1 for r in records if r["got"] == a and r["want"] == a)
        fp = sum(1 for r in records if r["got"] == a and r["want"] != a)
        fn = sum(1 for r in records if r["got"] != a and r["want"] == a)
        tn = len(records) - tp - fp - fn
        out[a] = {"TP": tp, "FP": fp, "FN": fn, "TN": tn,
                  "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                  "recall": round(tp / (tp + fn), 3) if tp + fn else None}
    return out


def _prompt_text(router, agents) -> str:
    """The fixed part of the router prompt (persona, rules, examples), lower-cased, to spot test sentences in it."""
    if router is None:
        return ""
    return router._composer.route_stage(agents, "x", ()).system.lower()


async def _run_mode(mode: str, items: list[dict], profiles, router, agents) -> list[dict]:
    records = []
    prompt_text = _prompt_text(router, agents)
    for item in items:
        msg = item["say"]
        detail = match_agent_detail(msg, profiles)
        rules_pick = detail.agent or "responder"
        sure = is_confident(detail)
        got, via, ms = rules_pick, "rules", 0
        if mode == "model" or (mode == "hybrid" and not sure):
            decision = await router.route(msg, agents)
            if decision is not None:
                got, via, ms = decision.agent, "llm", decision.ms
            else:
                via = "fallback-rules"
        records.append({"say": msg, "want": item["agent"], "got": got, "via": via, "ms": ms,
                        "rules_pick": rules_pick, "rules_sure": sure, "ok": got == item["agent"],
                        "seen_in_prompt": (f'"{msg.lower()}"' in prompt_text) or (f"message: {msg.lower()}\n" in prompt_text)})
    return records


def _report(mode: str, records: list[dict]) -> dict:
    n = len(records)
    ok = sum(r["ok"] for r in records)
    tool_agents = {"lookup", "tasks", "memory", "system"}
    hijacks = [r for r in records if r["want"] == "responder" and r["got"] in tool_agents]
    lost = [r for r in records if r["want"] in tool_agents and r["got"] == "responder"]
    cross = [r for r in records if r["want"] in tool_agents and r["got"] in tool_agents and r["got"] != r["want"]]
    llm_ms = [r["ms"] for r in records if r["via"] == "llm"]
    unseen = [r for r in records if not r.get("seen_in_prompt")]
    print(f"\n=== mode: {mode} ===")
    if len(unseen) != n:  # some golden sentences are examples in the router prompt: report the fair number too
        u_ok = sum(r["ok"] for r in unseen)
        print(f"({n - len(unseen)} golden sentences appear verbatim in the router prompt; on the {len(unseen)} UNSEEN ones: "
              f"{u_ok}/{len(unseen)} = {u_ok / len(unseen):.1%})")
    print(f"accuracy {ok}/{n} = {ok / n:.1%}   | chat hijacked by a tool agent: {len(hijacks)}   "
          f"| tool request lost to chat: {len(lost)}   | wrong tool agent: {len(cross)}")
    if llm_ms:
        print(f"model calls: {len(llm_ms)}   latency ms median {int(statistics.median(llm_ms))}  "
              f"p95 {int(sorted(llm_ms)[max(0, int(len(llm_ms) * 0.95) - 1)])}  max {max(llm_ms)}")
    conf = _confusion(records)
    print(f"{'agent':10} {'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4}  precision recall")
    for a, c in conf.items():
        p = "-" if c["precision"] is None else f"{c['precision']:.2f}"
        r = "-" if c["recall"] is None else f"{c['recall']:.2f}"
        print(f"{a:10} {c['TP']:>4} {c['FP']:>4} {c['FN']:>4} {c['TN']:>4}  {p:>9} {r:>6}")
    for r in [x for x in records if not x["ok"]]:
        print(f"  MISS {r['say']!r}: got {r['got']} ({r['via']}), want {r['want']}")
    out = {"accuracy": round(ok / n, 4), "correct": ok, "cases": n,
           "accuracy_unseen": round(sum(r["ok"] for r in unseen) / len(unseen), 4) if unseen else None,
           "seen_in_prompt": n - len(unseen), "hijacks": len(hijacks), "lost_to_chat": len(lost),
           "wrong_tool_agent": len(cross), "per_agent": conf,
           "llm_ms_median": int(statistics.median(llm_ms)) if llm_ms else None}
    return out


def _sure_table(records: list[dict]) -> dict:
    """The keyword rules judged on their own: was the rules' pick right, split by 'sure' vs 'confused'."""
    sure_right = sum(1 for r in records if r["rules_sure"] and r["rules_pick"] == r["want"])
    sure_wrong = sum(1 for r in records if r["rules_sure"] and r["rules_pick"] != r["want"])
    conf_right = sum(1 for r in records if not r["rules_sure"] and r["rules_pick"] == r["want"])
    conf_wrong = sum(1 for r in records if not r["rules_sure"] and r["rules_pick"] != r["want"])
    n = len(records)
    print("\nKeyword rules judged on their own (the hybrid gate):")
    print(f"                       rules RIGHT   rules WRONG")
    print(f"  rules SURE          {sure_right:>8} (TP)   {sure_wrong:>8} (FP)   <- FP = sure but wrong: skips the model, mis-routes")
    print(f"  rules CONFUSED      {conf_right:>8} (FN)   {conf_wrong:>8} (TN)   <- FN = a wasted model call; TN = the model is needed")
    sure = sure_right + sure_wrong
    print(f"  the gate trusts the rules on {sure}/{n} messages ({sure / n:.0%}); of those, {sure_right}/{sure or 1} are right "
          f"({sure_right / (sure or 1):.1%} precision)")
    return {"sure_right_TP": sure_right, "sure_wrong_FP": sure_wrong, "confused_right_FN": conf_right,
            "confused_wrong_TN": conf_wrong, "gate_coverage": round(sure / n, 3),
            "gate_precision": round(sure_right / sure, 3) if sure else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["keyword", "hybrid", "model", "all"], default="all")
    ap.add_argument("--golden", default=str(ROOT / "tests" / "eval" / "golden_set.yaml"))
    ap.add_argument("--split", choices=["all", "dev", "test"], default="all",
                    help="dev = the half to tune on; test = the held-out half (look at it only for the final number)")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--label", default="run")
    args = ap.parse_args()

    cfg = load_full_config()
    items = yaml.safe_load(Path(args.golden).read_text(encoding="utf-8"))
    if args.split != "all":
        items = [i for i in items if split_of(i["say"]) == args.split]
    manifests = YamlToolManifestStore().load_all()
    profiles = _profiles(cfg, manifests)
    agents = [(p.name, p.description) for p in profiles]
    modes = ["keyword", "hybrid", "model"] if args.mode == "all" else [args.mode]

    router, model_name = (None, "-")
    if any(m != "keyword" for m in modes):
        router, model_name = _build_router(cfg, manifests)
        asyncio.run(router.warmup(agents))
    print(f"golden cases: {len(items)} ({args.split})   router model: {model_name}   profile: {active_profile() or 'base'}")

    results = {}
    for mode in modes:
        records = asyncio.run(_run_mode(mode, items, profiles, router, agents))
        rep = _report(mode, records)
        if mode in ("keyword", "hybrid"):
            rep["rules_sure_table"] = _sure_table(records)
        results[mode] = {"summary": rep, "cases": records}

    if args.save:
        out_dir = ROOT / "tests" / "eval" / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"routing-{args.label}-{datetime.now():%Y%m%d-%H%M%S}.json"
        path.write_text(json.dumps({"router_model": model_name, "profile": active_profile(),
                                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                                    "modes": results}, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nsaved: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
