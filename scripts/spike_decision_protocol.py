"""Spike: which decision protocol is the most reliable on the real model? (docs/10, Phase 2)

    python scripts/spike_decision_protocol.py                       # all variants, 1 run per case
    python scripts/spike_decision_protocol.py --variants A1,C --repeats 3
    python scripts/spike_decision_protocol.py --limit 6             # quick smoke run

Variants (all on tests/eval/orchestrator_golden.yaml, the model in config/inference.yaml, temperature 0.1):
  A1  constrained control decode {needs_live_data, calls, clarification}, flag FIRST, with the ACTIVE: state line
  A2  same, calls FIRST
  A3  same as A1 but WITHOUT the ACTIVE: line (does session state earn its keep, or is history enough?)
  B   native Qwen tool calling: tools rendered by the chat template, free `<tool_call>` output, parsed after
      (the design in data/single-agent-design.md). Unconstrained.
  C   hybrid grammar: ONE generation that is either `<tool_call>{...}</tool_call>` (grammar-constrained to a real
      tool + valid args) or plain text (chat). One decode for chat turns.

A turn that needs a chat reply after the decision (A* with no calls) also runs a chat decode, so `turn_ms` is
comparable across variants. The per-case result is checked against `expect` (a `phase4:` block replaces it once the tool it names exists).
Results go to tests/eval/results/spike-protocol-<ts>.json.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.config import load_full_config  # noqa: E402
from domain.entities.control_decision import ControlDecision  # noqa: E402
from domain.entities.conversation import Turn  # noqa: E402
from domain.entities.session_context import SessionContext  # noqa: E402
from domain.policies.dispatch_policy import Route, resolve  # noqa: E402
from domain.policies.session_state_policy import active_line  # noqa: E402
from domain.policies.tool_call_schema import build_control_schema, tool_args_schema  # noqa: E402
from service.prompting.prompt_composer import PromptComposer  # noqa: E402
from tpa.filestore.file_prompt_store import FilePromptStore  # noqa: E402
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore  # noqa: E402

NOW = datetime.now()
TEMPERATURE = 0.1
DECIDE_TOKENS = 200
CHAT_TOKENS = 80
_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


# ---- A6 prototype: an `intent` committed before the calls --------------------

INTENTS = ("request", "record", "chat", "unavailable", "no_context")
_INTENT_RULE = (
    "intent says what the user is doing. request: asks for what a listed tool does. record: states a task, a finished "
    "task or a fact to save. chat: general talk, knowledge, a mention, a quote, a complaint, technical talk. "
    "unavailable: needs live or private data that no tool provides. no_context: only continues something but there is "
    "nothing to continue. Decide intent FIRST; calls must be empty unless intent is request or record.\n\n"
)


def _intent_of(d: dict) -> str:
    calls = d.get("calls") or []
    if calls:
        c = calls[0]
        record = (c["tool"] == "tasks" and c["args"].get("action") in ("add", "complete")) or (c["tool"] == "remember" and c["args"].get("action") == "save")
        return "record" if record else "request"
    if d.get("clarification"):
        return "no_context"
    return "unavailable" if d.get("needs_live_data") else "chat"


def _with_intent(system: str) -> str:
    out = []
    for line in system.splitlines():
        if line.startswith('{"needs_live_data"'):
            try:
                d = json.loads(line)
                line = json.dumps({"intent": _intent_of(d), **d}, separators=(",", ":"), ensure_ascii=False)
            except ValueError:
                pass
        out.append(line)
    text = "\n".join(out)
    return text.replace("Examples:\n", _INTENT_RULE + "Examples:\n", 1)


# ---- checking ---------------------------------------------------------------

def _args_match(expected: dict, actual: dict) -> bool:
    for key, want in (expected or {}).items():
        got = (actual or {}).get(key)
        if got is None:
            return False
        if isinstance(want, bool):
            if got is not want:
                return False
        elif isinstance(want, (int, float)):
            try:
                if float(got) != float(want):
                    return False
            except (TypeError, ValueError):
                return False
        elif str(want).lower() not in str(got).lower():
            return False
    return True


def judge(expect: dict, calls: list[dict], needs_live, clarification) -> tuple[bool, str]:
    """(ok, failure class). Failure classes map to harm: MISSED_TOOL (high), FALSE_CALL, WRONG_CALL, FLAG, CLARIFY."""
    want = expect["calls"]
    if want:
        if not calls:
            return False, "MISSED_TOOL"
        if [c["tool"] for c in calls] != [w["tool"] for w in want]:
            return False, "WRONG_CALL"
        if not all(_args_match(w.get("args", {}), c.get("args", {})) for w, c in zip(want, calls)):
            return False, "WRONG_ARGS"
        return True, ""
    if calls:
        return False, "FALSE_CALL"
    if "needs_live_data" in expect and needs_live is not None and needs_live != expect["needs_live_data"]:
        return False, "FLAG"
    if expect.get("clarification") and needs_live is not None and not clarification:
        return False, "CLARIFY"   # only decidable where the protocol has a clarification channel (A*)
    return True, ""


def judge_route(expect: dict, res) -> tuple[bool, str]:
    """Score what the PIPELINE does (model output + dispatch_policy.resolve), not the raw model output.

    ASKED_INSTEAD = the pipeline asked a question where a call was expected: a safe, recoverable miss.
    A wrong or guessed destructive target is turned into a question by the target veto, so it lands here, not as a write."""
    want = expect["calls"]
    if want:
        if res.route is not Route.TOOLS:
            return False, "ASKED_INSTEAD" if res.route is Route.CLARIFY else "MISSED_TOOL"
        got = list(res.calls)
        if [c["tool"] for c in got] != [w["tool"] for w in want]:
            return False, "WRONG_CALL"
        if not all(_args_match(w.get("args", {}), c.get("args", {})) for w, c in zip(want, got)):
            return False, "WRONG_ARGS"
        return True, ""
    if res.route is Route.TOOLS:
        return False, "FALSE_CALL"
    if res.route is Route.FAIL_CLOSED:
        return False, "INVALID"
    if expect.get("clarification"):
        return (res.route is Route.CLARIFY), ("" if res.route is Route.CLARIFY else "NO_CLARIFY")
    if "needs_live_data" in expect:
        wanted = Route.REFUSE if expect["needs_live_data"] else Route.CHAT
        if res.route is not wanted:
            return False, "FLAG" if res.route is not Route.CLARIFY else "ASKED_INSTEAD"
    return True, ""


# ---- model ------------------------------------------------------------------

class Model:
    def __init__(self, cfg):
        import llama_cpp

        inf = cfg.inference
        self._llama_cpp = llama_cpp
        self.llm = llama_cpp.Llama(model_path=str(ROOT / inf.model_path), n_ctx=inf.n_ctx, n_threads=inf.n_threads, verbose=False)
        if inf.prompt_cache_mb > 0:
            self.llm.set_cache(llama_cpp.LlamaRAMCache(capacity_bytes=inf.prompt_cache_mb * 1024 * 1024))

    def chat(self, system: str, user: str, *, max_tokens: int, schema=None, grammar=None, tools=None, history=(), temperature=None) -> tuple[str, float, dict]:
        messages = [{"role": "system", "content": system}]
        for h in history:
            messages += [{"role": "user", "content": h["user"]}, {"role": "assistant", "content": h["veda"]}]
        messages.append({"role": "user", "content": f"{user}\n/no_think"})
        kw = {"messages": messages, "max_tokens": max_tokens, "temperature": TEMPERATURE if temperature is None else temperature}
        if schema is not None:
            kw["response_format"] = {"type": "json_object", "schema": schema}
        if grammar is not None:
            kw["grammar"] = grammar
        if tools is not None:
            kw["tools"] = tools
        t0 = time.perf_counter()
        out = self.llm.create_chat_completion(**kw)
        ms = (time.perf_counter() - t0) * 1000
        return _THINK.sub("", out["choices"][0]["message"]["content"] or "").strip(), ms, out.get("usage", {})


def hybrid_grammar(llama_cpp, call_item_schema: dict):
    """root := 1-3 `<tool_call>{valid call}</tool_call>` lines | free text that does not start with '<'."""
    gbnf = llama_cpp.llama_grammar.json_schema_to_gbnf(json.dumps(call_item_schema))
    gbnf = re.sub(r"^root ::=", "call ::=", gbnf, count=1, flags=re.MULTILINE)
    gbnf += (
        '\nroot ::= calls | chat\n'
        'calls ::= "<tool_call>" call "</tool_call>" ("\\n<tool_call>" call "</tool_call>"){0,2}\n'
        'chat ::= [^<\\x00] [^\\x00]*\n'
    )
    return llama_cpp.LlamaGrammar.from_string(gbnf, verbose=False)


# ---- variants ---------------------------------------------------------------

class Runner:
    def __init__(self, model: Model, composer: PromptComposer, manifests):
        self.m, self.composer, self.manifests = model, composer, manifests
        self.manifest_map = {m.name: m for m in manifests}
        self.persona = composer._prompts.get("persona_lite")
        self.schema_flag_first = build_control_schema(manifests)
        self.schema_calls_first = build_control_schema(manifests, key_order=("calls", "needs_live_data", "clarification"))
        props = {"intent": {"enum": list(INTENTS)}, **self.schema_flag_first["properties"]}
        self.schema_intent = {**self.schema_flag_first, "properties": props, "required": ["intent", *self.schema_flag_first["required"]]}
        self.tools_native = [
            {"type": "function", "function": {"name": x.name, "description": x.description, "parameters": tool_args_schema(x)}}
            for x in manifests
        ]
        item = self.schema_flag_first["properties"]["calls"]["items"]
        self.grammar_c = hybrid_grammar(model._llama_cpp, item)
        sigs = "\n".join(composer._signature(x) for x in manifests)
        self.system_c = (
            f"{self.persona}\n\nTools:\n{sigs}\n\n"
            'When the user is asking you to use a tool, reply with ONLY <tool_call>{"tool": "<name>", "args": {...}}</tool_call> '
            "(one per line, at most 3). Mentioning a topic, quoting someone, or saying what they do not want is not a request. "
            "Otherwise just answer in plain spoken text. Never claim you did something you did not do."
        )
        self.system_b = (
            f"{self.persona}\n\nUse a tool only when the user is asking for what it does. Mentioning a topic, quoting "
            "someone, or saying what they do not want is not a request. Otherwise answer in plain spoken text."
        )

    @staticmethod
    def _turns(history):
        out = []
        for h in history or []:
            out += [Turn(None, "s", "user", h["user"], NOW), Turn(None, "s", "assistant", h["veda"], NOW)]
        return out

    def _chat_followup(self, case) -> float:
        _, ms, _ = self.m.chat(self.persona, case["say"], max_tokens=CHAT_TOKENS, history=case.get("history", []))
        return ms

    def run(self, variant: str, case: dict) -> dict:
        r = {"say": case["say"], "category": case["category"]}
        history, state = case.get("history", []), case.get("state")
        if variant in ("A1", "A2", "A3", "A5", "A6"):
            active = ""
            if state and variant != "A3":
                active = active_line(SessionContext("s", "", state["tool"], state.get("slots", {}), NOW - timedelta(minutes=3),
                                                    NOW + timedelta(minutes=12)), NOW)
            order = ("calls", "needs_live_data", "clarification") if variant == "A2" else ("needs_live_data", "calls", "clarification")
            schema = self.schema_calls_first if variant == "A2" else self.schema_flag_first
            p = self.composer.control_stage(case["say"], self._turns(history), active, key_order=order, show_empty=True)
            system = p.system
            if variant == "A6":   # prototype: commit to an intent BEFORE the calls
                schema, system = self.schema_intent, _with_intent(system)
            raw, ms, usage = self.m.chat(system, p.prompt, max_tokens=DECIDE_TOKENS, schema=schema,
                                         temperature=0.0 if variant == "A5" else None)
            r.update(decide_ms=ms, prompt_tokens=usage.get("prompt_tokens"), out_tokens=usage.get("completion_tokens"), raw=raw)
            try:
                d = json.loads(raw)
                calls, needs, clar = d["calls"], d["needs_live_data"], d["clarification"]
                r["valid"] = True
            except (ValueError, KeyError, TypeError):
                calls, needs, clar, r["valid"] = [], None, None, False
            r["turn_ms"] = ms + (self._chat_followup(case) if not calls and not clar and needs is False else 0)
        elif variant == "B":
            user = f"TODAY: {NOW.strftime('%A %d %b %Y, %H:%M')}.\nUSER: {case['say']}"
            raw, ms, usage = self.m.chat(self.system_b, user, max_tokens=DECIDE_TOKENS, tools=self.tools_native, history=history)
            calls, bad = [], False
            for blob in _TOOL_CALL.findall(raw):
                try:
                    d = json.loads(blob)
                    calls.append({"tool": d.get("name"), "args": d.get("arguments") or {}})
                except ValueError:
                    bad = True
            names = {x.name for x in self.manifests}
            r["valid"] = not bad and all(c["tool"] in names for c in calls) and not ("<tool_call>" in raw and not calls)
            needs = clar = None
            r.update(decide_ms=ms, turn_ms=ms, prompt_tokens=usage.get("prompt_tokens"), out_tokens=usage.get("completion_tokens"), raw=raw)
        elif variant == "C":
            user = f"TODAY: {NOW.strftime('%A %d %b %Y, %H:%M')}.\nUSER: {case['say']}"
            raw, ms, usage = self.m.chat(self.system_c, user, max_tokens=DECIDE_TOKENS, grammar=self.grammar_c, history=history)
            calls, bad = [], False
            for blob in _TOOL_CALL.findall(raw):
                try:
                    d = json.loads(blob)
                    calls.append({"tool": d["tool"], "args": d["args"]})
                except (ValueError, KeyError, TypeError):
                    bad = True   # the grammar should make this impossible; count it if it ever happens
            r["valid"] = not bad and (bool(calls) or not raw.lstrip().startswith("<"))
            needs = clar = None
            r.update(decide_ms=ms, turn_ms=ms, prompt_tokens=usage.get("prompt_tokens"), out_tokens=usage.get("completion_tokens"), raw=raw)
        else:
            raise SystemExit(f"unknown variant {variant}")
        r["calls"], r["needs_live_data"], r["clarification"] = calls, needs, clar
        if variant.startswith("A"):
            res = resolve(ControlDecision(calls=tuple(calls), needs_live_data=needs, clarification=clar, valid=r["valid"]),
                          self.manifest_map, user_message=case["say"])
            r["route"] = res.route.value
            r["ok"], r["failure"] = judge_route(case["expect"], res)
        else:
            r["ok"], r["failure"] = judge(case["expect"], calls, needs, clar)
        return r


# ---- report -----------------------------------------------------------------

def summarise(name: str, rows: list[dict]) -> None:
    n = len(rows)
    ok = sum(r["ok"] for r in rows)
    fails = defaultdict(int)
    for r in rows:
        if not r["ok"]:
            fails[r["failure"]] += 1
    invalid = sum(not r["valid"] for r in rows)
    turn = sorted(r["turn_ms"] for r in rows)
    dec = sorted(r["decide_ms"] for r in rows)
    p95 = lambda xs: xs[min(len(xs) - 1, int(len(xs) * 0.95))]
    print(f"\n=== {name}: {ok}/{n} correct ({100 * ok / n:.0f}%)   invalid/unparseable output: {invalid}")
    print(f"    failures: {dict(fails) or 'none'}")
    print(f"    decision ms  median {statistics.median(dec):.0f}  p95 {p95(dec):.0f}   |   full-turn ms  median {statistics.median(turn):.0f}  p95 {p95(turn):.0f}")
    cat = defaultdict(lambda: [0, 0])
    for r in rows:
        cat[r["category"]][0] += r["ok"]
        cat[r["category"]][1] += 1
    print("    " + "  ".join(f"{c}:{a}/{b}" for c, (a, b) in sorted(cat.items())))
    for r in rows:
        if not r["ok"]:
            print(f"      x {r['failure']:<11} {r['say']!r}  ->  calls={[(c['tool'], c['args']) for c in r['calls']]} live={r['needs_live_data']} clar={r['clarification']!r}")


def apply_phase4(cases: list[dict], tool_names: set[str]) -> None:
    """A `phase4:` block replaces `expect` once the tools it names exist (get_weather_forecast)."""
    for case in cases:
        override = case.get("phase4")
        if override and {c["tool"] for c in override.get("calls", [])} <= tool_names:
            case["expect"] = override


def rejudge(path: Path, cases: list[dict]) -> int:
    """Re-score saved raw model outputs (no model needed): the model's answers do not change when the golden set,
    the judge or the dispatch policy do, so those can be iterated on for free."""
    by_say = {c["say"]: c for c in cases}
    manifests = {m.name: m for m in YamlToolManifestStore().load_all()}
    saved = json.loads(path.read_text(encoding="utf-8"))
    for variant, rows in saved.items():
        if not variant.startswith("A"):
            continue
        out = []
        for r in rows:
            case = by_say.get(r["say"])
            if case is None:
                continue
            res = resolve(ControlDecision(calls=tuple(r["calls"]), needs_live_data=r["needs_live_data"],
                                          clarification=r["clarification"], valid=r["valid"]), manifests, user_message=r["say"])
            ok, failure = judge_route(case["expect"], res)
            out.append({**r, "route": res.route.value, "ok": ok, "failure": failure, "decide_ms": r["decide_ms"], "turn_ms": r["turn_ms"]})
        summarise(f"{variant} (pipeline view, {len(out)} cases)", out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="A1,A2,A3,B,C")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--golden", default=str(ROOT / "tests" / "eval" / "orchestrator_golden.yaml"))
    ap.add_argument("--rejudge", default="", help="re-score a saved spike JSON offline with the CURRENT judge, golden set and dispatch policy (no model)")
    ap.add_argument("--phase4", action="store_true", help="with --rejudge: apply the `phase4:` expectations (the saved run had the forecast tool)")
    args = ap.parse_args()

    cases = yaml.safe_load(Path(args.golden).read_text(encoding="utf-8"))
    if args.rejudge:
        if args.phase4:   # the saved run was made with the forecast tool present
            apply_phase4(cases, {m.name for m in YamlToolManifestStore().load_all()})
        return rejudge(Path(args.rejudge), cases)
    if args.limit:
        cases = cases[: args.limit]
    cfg = load_full_config()
    manifests = YamlToolManifestStore().load_all()
    apply_phase4(cases, {m.name for m in manifests})   # not for --rejudge unless --phase4: old saved runs predate the tool
    composer = PromptComposer(FilePromptStore(), manifests)
    print(f"loading {cfg.inference.model_path} ... ({len(cases)} cases x {args.repeats} repeat(s))", flush=True)
    runner = Runner(Model(cfg), composer, manifests)

    results: dict[str, list[dict]] = {}
    out = ROOT / "tests" / "eval" / "results" / f"spike-protocol-{time.strftime('%Y%m%d-%H%M%S')}.json"
    for variant in [v.strip() for v in args.variants.split(",") if v.strip()]:
        runner.run(variant, {"say": "hello there", "category": "warmup", "expect": {"calls": []}})  # warm the prefix cache
        rows = []
        for rep in range(args.repeats):
            for i, case in enumerate(cases, 1):
                row = runner.run(variant, case)
                row["repeat"] = rep
                rows.append(row)
                print(f"  [{variant} {rep + 1}/{args.repeats} {i}/{len(cases)}] {'ok ' if row['ok'] else 'ERR'} {row['decide_ms']:.0f} ms  {case['say'][:50]!r}", flush=True)
        results[variant] = rows
        summarise(variant, rows)
        out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")   # incremental: a crash keeps finished variants
    print(f"\nsaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
