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
import statistics
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.config import load_full_config  # noqa: E402
from domain.entities.agent_profile import AgentProfile  # noqa: E402
from domain.policies.routing_policy import pick_agent  # noqa: E402
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
    ap.add_argument("--golden", default=str(ROOT / "tests" / "eval" / "golden_set.yaml"))
    args = ap.parse_args()

    items = yaml.safe_load(Path(args.golden).read_text(encoding="utf-8"))
    manifests = YamlToolManifestStore().load_all()
    profiles = _profiles(manifests)
    for item in items:
        item["_routed"] = pick_agent(item["say"], profiles, "responder")

    route_ok = [i["_routed"] == i["agent"] for i in items]
    print(f"routing: {sum(route_ok)}/{len(items)} correct")
    for item, ok in zip(items, route_ok):
        if not ok:
            print(f"  MISROUTED {item['say']!r}: got {item['_routed']}, expected {item['agent']}")
    accuracy = sum(route_ok) / len(items)

    if args.model:
        from tpa.inference.llama_cpp_client import LlamaCppClient

        cfg = load_full_config()
        model_path = Path(cfg.inference.model_path)
        client = LlamaCppClient(model_path if model_path.is_absolute() else ROOT / model_path, n_ctx=cfg.inference.n_ctx)
        mmap = {m.name: m for m in manifests}
        composer = PromptComposer(FilePromptStore(), manifests, count_tokens=client.count_tokens)
        runner = ToolTurnRunner(
            client=client, composer=composer, skill_runner=None, manifests=mmap, guard=ToolUseGuard(manifests),
        )
        outputs = asyncio.run(_decide_all(items, manifests, composer, runner))

        tool_ok, arg_ok, scored, latencies, tokens = 0, 0, 0, [], []
        for item, out in zip(items, outputs):
            if out is None:
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
            if not good:
                print(f"  DECIDE MISS {item['say']!r}: expected {want_tool} {item.get('args')}, got {calls}")
        print(f"decide stage: tool {tool_ok}/{scored}, tool+args {arg_ok}/{scored}")
        print(
            f"call-stage prompt tokens: median {int(statistics.median(tokens))}, max {max(tokens)}   "
            f"decide latency ms: median {int(statistics.median(latencies))}, p95 {int(sorted(latencies)[int(len(latencies) * 0.95) - 1])}"
        )
        accuracy = min(accuracy, arg_ok / scored)

    print(f"overall accuracy: {accuracy:.0%} (gate {args.min_accuracy:.0%})")
    return 0 if accuracy >= args.min_accuracy else 1


if __name__ == "__main__":
    raise SystemExit(main())
