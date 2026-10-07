"""Baseline the CURRENT routing against the orchestrator golden set (docs/10, Phase 0).

    python scripts/eval_orchestrator.py            # rules-only: what today's keyword router does, no model needed
    python scripts/eval_orchestrator.py --save     # also write tests/eval/results/orchestrator-baseline-<ts>.json

For each case it reports which agent today's rules pick and whether that agent could even
own the expected tool (a case routed to `responder` that expects a call is a missed tool call:
Bug A). Cases expecting no call that route to a tool agent are the false-positive risk (Bug B).
The model half (decode accuracy, latency) needs the device and lands with the orchestrator.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from domain.entities.agent_profile import AgentProfile  # noqa: E402
from domain.policies.routing_policy import pick_agent  # noqa: E402
from service.agent.tool_use_guard import ToolUseGuard  # noqa: E402
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(ROOT / "tests" / "eval" / "orchestrator_golden.yaml"))
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    cases = yaml.safe_load(Path(args.golden).read_text(encoding="utf-8"))
    manifests = YamlToolManifestStore().load_all()
    owner = {m.name: m.agent for m in manifests}
    triggers: dict[str, list[str]] = {}
    for m in manifests:
        triggers.setdefault(m.agent, []).extend(m.triggers)
    profiles = (AgentProfile(name="responder", description="General conversation", model_alias=""),) + tuple(
        AgentProfile(name=a, description=a, model_alias="", triggers=tuple(t)) for a, t in triggers.items()
    )
    guard = ToolUseGuard(manifests)

    rows, by_cat = [], defaultdict(lambda: [0, 0])
    for c in cases:
        want = c["expect"]["calls"]
        routed = pick_agent(c["say"], profiles, "responder")
        forced = bool(guard.required_tools(c["say"]))
        if want:
            ok = routed == owner[want[0]["tool"]]
            verdict = "ok" if ok else "MISSED_TOOL (routed to %s)" % routed
        else:
            ok = routed == "responder"
            verdict = "ok" if ok else "FALSE_POSITIVE (routed to %s%s)" % (routed, ", would force a call" if forced else "")
        by_cat[c["category"]][0] += ok
        by_cat[c["category"]][1] += 1
        rows.append({"say": c["say"], "category": c["category"], "routed": routed, "force_regex": forced, "ok": ok, "verdict": verdict})

    total_ok = sum(r["ok"] for r in rows)
    print(f"current rules-only routing: {total_ok}/{len(rows)} cases route as the orchestrator golden set expects\n")
    for cat, (ok, n) in sorted(by_cat.items()):
        print(f"  {cat:<12} {ok}/{n}")
    print()
    for r in rows:
        if not r["ok"]:
            print(f"  {r['verdict']:<55} [{r['category']}] {r['say']!r}")

    if args.save:
        out = ROOT / "tests" / "eval" / "results" / f"orchestrator-baseline-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps({"mode": "rules-only", "ok": total_ok, "total": len(rows), "rows": rows}, indent=2), encoding="utf-8")
        print(f"\nsaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
