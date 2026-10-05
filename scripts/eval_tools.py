"""Evaluate the tool harness against the golden set.

    python scripts/eval_tools.py            # routing only (deterministic, instant)
    python scripts/eval_tools.py --model    # also run the real model's decide stage

For every utterance: routing (does the supervisor pick the right agent?) and,
with --model, the decide stage (does the constrained model pick the right tool
with the right arguments, or correctly return no call?). Also reports prompt
tokens and decide latency, so a prompt or manifest change can be judged by
numbers instead of impressions.

Exit code is non-zero if accuracy is below --min-accuracy, so it can gate CI.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.config import load_full_config  # noqa: E402
from domain.entities.agent_profile import AgentProfile  # noqa: E402
from domain.policies.routing_policy import match_agent  # noqa: E402
from service.agent.llm_router import LlmRouter  # noqa: E402
from service.agent.tool_turn_runner import ToolTurnRunner  # noqa: E402
from service.agent.tool_use_guard import ToolUseGuard  # noqa: E402
from service.prompting.prompt_composer import PromptComposer  # noqa: E402
from tpa.filestore.file_prompt_store import FilePromptStore  # noqa: E402
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore  # noqa: E402


def _profiles(manifests):
    triggers: dict[str, list[str]] = {}
    for m in manifests:
        triggers.setdefault(m.agent, []).extend(m.triggers)
    chat = AgentProfile(name="responder", description="General conversation, Q&A, brainstorming", model_alias="")
    return (chat,) + tuple(AgentProfile(name=a, description=a, model_alias="", triggers=tuple(t)) for a, t in triggers.items())


def _args_match(expected: dict, actual: dict) -> bool:
    for key, want in (expected or {}).items():
        got = actual.get(key)
        if got is None:
            return False
        if isinstance(want, str):
            if want.lower() not in str(got).lower():
                return False
        elif isinstance(want, bool):
            if got is not want:
                return False
        elif isinstance(want, (int, float)):
            try:
                if float(got) != float(want):
                    return False
            except (TypeError, ValueError):
                return False
        elif got != want:
            return False
    return True


async def _decide_all(items, manifests, composer, runner):
    results = []
    for item in items:
        if item["_routed"] == "responder":
            results.append(None)  # chat path: the model is not asked to pick a tool
            continue
        tool_names = composer.tools_of_agent(item["_routed"])
        prompt = composer.call_stage(tool_names, item["say"])
        started = time.perf_counter()
        calls = await runner._decide(prompt.system, prompt.prompt, composer.manifests_for(tool_names), min_calls=0)
        required = [t for t in runner._guard.required_tools(item["say"]) if t in tool_names]
        if not calls and required:  # mirror the runtime's forced retry (minItems=1)
            calls = await runner._decide(
                prompt.system, prompt.prompt, composer.manifests_for(required), min_calls=1
            )
        results.append({"calls": calls, "ms": int((time.perf_counter() - started) * 1000), "tokens": prompt.tokens})
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="store_true", help="also run the model's decide stage")
    ap.add_argument("--min-accuracy", type=float, default=0.85)
    ap.add_argument("--routing-only", action="store_true", help="with --model: only evaluate routing, skip the decide stage")
    ap.add_argument("--golden", default=str(ROOT / "tests" / "eval" / "golden_set.yaml"))
    ap.add_argument("--save", action="store_true",
                    help="with --model: write per-case results to tests/eval/results/<label>-<timestamp>.json")
    ap.add_argument("--label", default=None, help="name for the saved run (default: the model file name)")
    args = ap.parse_args()

    items = yaml.safe_load(Path(args.golden).read_text(encoding="utf-8"))
    manifests = YamlToolManifestStore().load_all()
    profiles = _profiles(manifests)
    for item in items:
        item["_rule"] = match_agent(item["say"], profiles)          # None = the rules did not recognise it
        item["_routed"] = item["_rule"] or "responder"               # rules-only: unmatched falls to chat

    route_ok = [i["_routed"] == i["agent"] for i in items]
    rule_items = [i for i in items if not i.get("llm")]
    for i in rule_items:
        if i["_routed"] != i["agent"]:
            print(f"  RULE MISROUTE {i['say']!r}: rules gave {i['_rule']}, expected {i['agent']}")
    print(f"rules only: {sum(i['_routed'] == i['agent'] for i in rule_items)}/{len(rule_items)} of the trigger-word cases; "
          f"{sum(route_ok)}/{len(items)} overall (the {len(items) - len(rule_items)} no-trigger cases need the LLM router)")
    accuracy = sum(i["_routed"] == i["agent"] for i in rule_items) / max(1, len(rule_items))

    if args.model:
        from tpa.inference.llama_cpp_client import LlamaCppClient

        cfg = load_full_config()
        model_path = Path(cfg.inference.model_path)
        client = LlamaCppClient(
            model_path if model_path.is_absolute() else ROOT / model_path,
            n_ctx=cfg.inference.n_ctx, prompt_cache_mb=cfg.inference.prompt_cache_mb,
        )
        mmap = {m.name: m for m in manifests}
        composer = PromptComposer(FilePromptStore(), manifests, count_tokens=client.count_tokens)
        runner = ToolTurnRunner(
            client=client, composer=composer, skill_runner=None, manifests=mmap, guard=ToolUseGuard(manifests),
        )
        # ---- LLM routing of everything the rules did not recognise ----
        agents = [
            (name, getattr(cfg.agents, name).description)
            for name in ("responder", "system", *sorted({m.agent for m in manifests}))
        ]
        router = LlmRouter(client, composer, num_predict=24)

        async def _route_all():
            out = []
            for item in items:
                if item["_rule"] is not None:
                    out.append(None)
                    continue
                d = await router.route(item["say"], agents)
                out.append(d)
            return out

        decisions = asyncio.run(_route_all())
        llm_n = llm_ok = tool_hijacks = 0
        lat = []
        for item, d in zip(items, decisions):
            if d is None and item["_rule"] is not None:
                continue
            llm_n += 1
            routed = d.agent if d is not None else "responder"
            if d is not None:
                lat.append(d.ms)
            item["_routed"] = routed
            if routed == item["agent"]:
                llm_ok += 1
            else:
                print(f"  LLM MISROUTE {item['say']!r}: got {routed}, expected {item['agent']}")
                if item["agent"] == "responder" and routed in ("tasks", "lookup"):
                    tool_hijacks += 1
        print(f"LLM router (messages the rules did not match): {llm_ok}/{llm_n} correct, "
              f"{tool_hijacks} chat message(s) wrongly sent to a tool agent; "
              f"latency ms median {int(statistics.median(lat))}, max {max(lat)}" if lat else "LLM router: nothing to route")
        accuracy = sum(i["_routed"] == i["agent"] for i in items) / len(items)
        print(f"routing with LLM router: {sum(i['_routed'] == i['agent'] for i in items)}/{len(items)}")

        if args.routing_only:
            print(f"overall accuracy: {accuracy:.0%} (gate {args.min_accuracy:.0%})")
            return 0 if accuracy >= args.min_accuracy else 1

        outputs = asyncio.run(_decide_all(items, manifests, composer, runner))

        tool_ok, arg_ok, scored, latencies, tokens = 0, 0, 0, [], []
        records = []
        for item, out in zip(items, outputs):
            record = {"say": item["say"], "expect_agent": item["agent"], "routed": item["_routed"],
                      "expect_tool": item["tool"], "expect_args": item.get("args")}
            records.append(record)
            if out is None:
                record.update(scored=False, ok=item["_routed"] == item["agent"])
                continue
            scored += 1
            latencies.append(out["ms"])
            tokens.append(out["tokens"])
            calls = out["calls"]
            want_tool = item["tool"]
            if want_tool is None:
                good = not calls
                tool_ok += good
                arg_ok += good
            else:
                chosen = calls[0] if calls else None
                good_tool = chosen is not None and chosen["tool"] == want_tool
                tool_ok += good_tool
                good_args = good_tool and _args_match(item.get("args"), chosen["args"])
                arg_ok += good_args
                good = good_args
            record.update(scored=True, ok=bool(good), got_calls=calls, decide_ms=out["ms"], prompt_tokens=out["tokens"])
            if not good:
                print(f"  DECIDE MISS {item['say']!r}: expected {want_tool} {item.get('args')}, got {calls}")
        print(f"decide stage: tool {tool_ok}/{scored}, tool+args {arg_ok}/{scored}")
        print(
            f"call-stage prompt tokens: median {int(statistics.median(tokens))}, max {max(tokens)}   "
            f"decide latency ms: median {int(statistics.median(latencies))}, p95 {int(sorted(latencies)[int(len(latencies) * 0.95) - 1])}"
        )
        accuracy = min(accuracy, arg_ok / max(1, scored))

        if args.save:
            import json
            from datetime import datetime

            label = args.label or model_path.stem
            by_tool: dict[str, dict] = {}
            for r in records:
                key = r["expect_tool"] or "chat(no tool)"
                t = by_tool.setdefault(key, {"n": 0, "ok": 0})
                t["n"] += 1
                t["ok"] += bool(r["ok"])
            summary = {
                "label": label, "model_path": str(cfg.inference.model_path),
                "n_ctx": cfg.inference.n_ctx, "profile": os.environ.get("VEDA_PROFILE", ""),
                "timestamp": datetime.now().isoformat(timespec="seconds"), "cases": len(items),
                "routing_correct": sum(i["_routed"] == i["agent"] for i in items),
                "decide_tool_correct": tool_ok, "decide_tool_and_args_correct": arg_ok, "decide_scored": scored,
                "decide_ms_median": int(statistics.median(latencies)),
                "decide_ms_p95": int(sorted(latencies)[int(len(latencies) * 0.95) - 1]),
                "prompt_tokens_median": int(statistics.median(tokens)),
                "by_tool": by_tool,
            }
            out_dir = ROOT / "tests" / "eval" / "results"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{label}-{datetime.now():%Y%m%d-%H%M%S}.json"
            out_path.write_text(json.dumps({"summary": summary, "cases": records}, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"saved: {out_path.relative_to(ROOT)}")
            print("per tool (ok/n): " + ", ".join(f"{k} {v['ok']}/{v['n']}" for k, v in sorted(by_tool.items())))

    print(f"overall accuracy: {accuracy:.0%} (gate {args.min_accuracy:.0%})")
    return 0 if accuracy >= args.min_accuracy else 1


if __name__ == "__main__":
    raise SystemExit(main())
