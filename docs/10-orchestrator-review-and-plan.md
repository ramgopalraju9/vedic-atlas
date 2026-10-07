# 10 — Review of `unified-assistant-orchestrator.md` + revised implementation plan

Reviewed 2026-10-07 against branch `release/v1.1` (HEAD `1771944`).
Input: `docs/unified-assistant-orchestrator.md` (the "design doc"), written by another agent.
Everything below marked **[verified]** was checked against the code in this session.

---

## 1. Current architecture (as it actually is today)

```
CLI / voice_session ──► SupervisorAgent.execute[_stream]
                          └─ _pick(ctx)  — routing_mode: keyword | hybrid | model | None(legacy)
                               keyword rules  = RouterPolicy → routing_policy.match_agent(message, profiles)  (no history)
                               model router   = LlmRouter (Qwen3-0.6B, 2nd resident model, {"agent": name})
                          └─ governance.check_action("route_to_agent")
                          └─ agent.execute
        ┌──────────────┬──────────────┬─────────────┬──────────────────┬──────────────┐
        ToolAgent      ToolAgent      ToolAgent     SystemAgent        ResponderAgent
        "lookup"       "tasks"        "memory"      own planner call,  chat only; persona+facts+
                                                    NO json_schema,    RAG+history; no tools
        ToolTurnRunner: decide (call_stage, schema-constrained) → [FORCE if required_when matched & no call]
                        → execute via SkillRunner → reply (template | narrate_stage + is_grounded)
        no-tool → fallback ResponderAgent, then guard.claims_action veto
```

Decision points today: **four** — keyword router, LLM router, `ToolTurnRunner._decide`, `SystemAgent` planner.
Composition root: `src/server.py::bootstrap` (lines ~842–910 wire guard, composer, runner, agents, supervisor).

### Bugs the design doc targets [verified]
- **A (false negative):** `match_agent(message, candidates)` sees only the newest message. "Should I bring an umbrella?" after Tokyo weather → no trigger → `responder` → ungrounded answer. (`test_golden_routing.py` already fails on "do I need an umbrella today", "is it hot in Jaipur today" etc.)
- **B (false positive):** `required_when` regex forces `minItems=1` retry in `tool_turn_runner.py:116-127` even when the model correctly returned `calls: []`.
- **P13:** `ResponderAgent._without_action_claims` (`responder.py:138`) drops the assistant reply *and the user turn before it* from chat history.
- `ConversationManager.turns` is global across sessions (`recent_turns` has no session filter).
- `ResponderAgent._render_history` has no per-turn clip / token budget (uses `max_history_turns=20`).

### Test baseline today
`pytest tests` → **514 passed, 6 failed** (all in `test_golden_routing.py`: Jaipur/umbrella/Goa/cloudy/"who are you"/bitcoin). Phase 0 must start from this, not from green.

---

## 2. Target architecture (from the design doc, condensed)

One decision per turn: `AssistantOrchestrator` does a single schema-constrained **control decode** →
`{calls[≤3], needs_live_data, clarification}`, with all tool signatures in one byte-identical cached system prefix and
a volatile tail `ACTIVE (session state) + RECENT (2 exchanges) + TODAY + USER`.

Dispatch: calls→execute via SkillRunner; clarification→ask; no calls + `needs_live_data=false`→`ResponderAgent` chat;
no calls + `needs_live_data=true`→fixed refusal. Reply: template tools never go back through the model; only
`returns: document` tools reach a generalised `content_stage` (data plane). `claims_action` becomes a backstop on every path.
Agents: 6 → 2 (`AssistantOrchestrator`, `ResponderAgent`). New `session_context` table (per session+speaker, TTL 15 min, `pending` clarification).
Deleted: keyword router, LLM router, `required_when` forcing, `SystemAgent` planner, `_without_action_claims`.
Principles: P1 no regex reads user input to decide actions; P2 one decision/one decode; P4 fail closed; static prompt is cheap, extra decodes are not.

---

## 3. Review verdict

**The diagnosis and the destination are right.** Bug A and B are real, share one root cause, and collapsing four decision
points into one grammar-constrained decode is the correct fix. I would adopt the architecture. But the doc is **not safe to
hand to an agent as-is**: it is stale in places, has ordering bugs between phases, and leaves two core concepts undefined.

### 3.1 Factual drift (doc vs. repo) — must be fixed before implementing
| # | Doc says | Reality |
|---|---|---|
| D1 | `llm_routing: false`; "no second resident model" is an anti-pattern | Commit `c3497ce` added `config/routing.yaml` with **`mode: hybrid`** and a resident Qwen3-0.6B router (`model_path: data/Qwen3-0.6B-Q4_K_M.gguf`). That is the thing the doc argues against, and it is **on by default today**. |
| D2 | Delete list: `LlmRouter`, `route_stage`, `build_route_schema`, `llm_routing` | Also must go: `config/routing.yaml`, `cfg.routing`, `routing_mode`/`router_on_failure` ctor args, `routing_status()`/`warm_router()` (used by `/health` + `server.py:1019`), `tests/test_routing_modes.py`, `tests/test_router_timeouts.py`, `scripts/eval_routing.py`, `match_agent_detail`/`is_confident`. |
| D3 | Phase 1 threads `session_id` through `chat.py` and `ChatRequest` | Commit `1771944` **deleted** `routes/chat.py` and `ChatRequest`. Entry points are now `routes/stream.py` (`StreamRequest`) and `voice_session.py:414` only. |
| D4 | References `09-agent-architecture-redesign.md` | File does not exist in `docs/`. |
| D5 | Doc is titled "10 —" | Fine, but `docs/00-overview.md`–`08-tool-harness.md` will need updating at the end (project convention). |
| D6 | `terminal`, `file_ops` get `destructive: true` manifests (§4.5) | No such manifests exist (`config/tools/` has 5: currency, remember, tasks, weather, web_search). They are skills listed under `system:` in `agents.yaml`. Decide: expose or not (see Q-A). |
| D7 | Latency: routing 5.5→2.4 s warm (inference.yaml) | `agents.yaml` says the routing call takes **30 s+ on a Pi**. The two measurements disagree; the doc's "+~1 s on chat turns" for the extra control decode is unproven on the Pi. |

Things the doc got right [verified]: `n_ctx 4096 / prompt_cache_mb 768`; `required_when` forcing; `_static_cache` keyed by `tuple(tool_names)`;
`call_history_turns=2`; `MAX_CALLS=3` + `oneOf`; `SystemAgent` has no `json_schema` and bypasses `SkillRunner`; no entry point supplies a session id;
`current_session_id()` is global/time-derived; `ConversationManager.turns` is global; `SkillRunner.execute_skill(ctx, name, **params)` returns `SkillResult` (raises `AppException` on missing/disabled skill).

### 3.2 Design gaps
- **G1 — `capability` is undefined.** Session state stores `capability="weather"`, but manifests only have `agent` (`lookup`/`tasks`/`memory`) and `name`. Weather and currency share agent `lookup`. *Fix:* drop `capability`; key state on `tool` name (+ validated slots), or add an explicit `capability` field to the manifest. I recommend tool name — fewer concepts.
- **G2 — `needs_live_data` contradicts the chat path.** Definition lists "my saved facts" as live/private → `true` → refusal. But saved facts are already injected into the chat prompt (`knowledge.get_context()`), and `remember` only has `list`. "What's my favourite sweet?" would be refused today-correct behaviour broken. *Fix:* remove "saved facts" from the `true` list (chat answers them); keep "my tasks/email/calendar" (tool-only).
- **G3 — Phase order bugs.** (a) Phase 4 adds `destructive: true` to YAML but the manifest field lands in Phase 5. (b) Phase 1/2 need `destructive`/`prompt_example` to render/write state. (c) Phase 3 deletes the legacy path *before* Phase 6 flips the flag default, making the flag meaningless and rollback a git revert. *Fix:* manifest fields go first (new Phase 1); cutover (flip default) precedes deletion; delete only after a soak.
- **G4 — `_persist` can't write state.** `ToolAgent._persist(ctx, reply)` doesn't receive the executed calls; the session-state writer needs `outcome.calls`. Small signature change; the orchestrator should own it.
- **G5 — Session id is resolved twice anyway.** `ConversationManager.add_turn` calls `repo.current_session_id()` per turn internally. Resolve-once in the orchestrator is not enough: `ConversationManager` needs an optional `session_id` param, or turns and state can straddle the 30-min boundary differently.
- **G6 — Field order in the control schema.** Grammar decoding emits keys in schema order. `calls` before `needs_live_data` means the flag is decoded *after* the decision it should inform. Put `needs_live_data` first and test both orders in the eval. Also verify llama.cpp's grammar converter accepts `oneOf` + `type: ["string","null"]` in one schema (spike before Phase 3).
- **G7 — `num_predict`.** Control output now holds up to 3 calls + 2 fields; current `call_num_predict=160` may truncate → unparseable → silent `[]`. Raise and test. Also a parse failure must fail **closed** (clarify/refuse), not silently become chat.
- **G8 — Governance hook.** `Supervisor.execute` runs `check_action("route_to_agent")`. With no routing it has no meaning; keep a check on the orchestrator call or drop it deliberately. Per-tool governance already happens through SkillRunner hooks.
- **G9 — Eval harness mismatch.** `tests/eval/golden_set.yaml` is keyed by `agent:` (routing target) + `tool:`. The doc's golden set needs `calls / needs_live_data / clarification`. Extend the file format; keep `scripts/eval_tools.py` working.
- **G10 — Project conventions.** New code must follow `feedback_dev_conventions`: port in `domain/ports/`, SQLAlchemy model in `tpa/persistence/models/` (+ add to the import list in `migrations.init_tables`) and repo in `tpa/persistence/repositories/`, constructed only in `server.py`; errors via `AppException` + `ExceptionCode` (+ row in `exceptions/handlers.py::_STATUS_BY_CODE`). The doc says "src/tpa/ SQLite adapter" loosely; this is the concrete shape.

### 3.3 Risks to measure, not assume
1. 4B-Q4 tool-selection accuracy with all tools + a longer prompt (doc's main risk — agree).
2. Chat turns now cost 2 decodes; on the Pi that may be 5–10 s, not +1 s (D7).
3. `needs_live_data` mis-set → wrongful refusal ("capital of Japan"). Fail-closed is safe but annoying.
4. Voice: STT errors feed the control decode; clarification loops are capped at 2 attempts (good).
5. Prefix cache: control + content + chat persona = 3 prefixes × ~70 MB inside 768 MB — fine, but the chat path must not evict the control prefix.

---

## 4. Revised phased implementation plan

Rules for every phase: read each file in full before editing; one phase at a time; each phase ends with the full test suite compared to the
baseline (514 pass / 6 known failures, then the golden set); old path stays behind `orchestrator_enabled` until Phase 7; follow layering + `AppException` conventions.

### Phase 0 — Baseline & decisions (no behaviour change)
- Fix this doc's drift (D1–D7, G1–G2) → one amended design doc; delete/replace the stale references.
- Extend `tests/eval/golden_set.yaml` with `calls`, `needs_live_data`, `clarification`, `session` (prior turn setup); add the new categories (follow-ups, mention-without-request, negation, quotation, meta, technical, no-state, multi-intent, destructive ambiguity, tool failure).
- Record baseline results of **current** code (`scripts/eval_tools.py`; there are already `tests/eval/results/*.json` to compare). Capture 3 Pi traces (`prompt_tokens`, `timings_ms`) incl. router latency (resolves D7).
- Add `TurnTrace` fields (`state_used, slots_inherited, needs_live_data, clarified, prefix_cache_hit`) with defaults; extend `SqliteTraceRepository` (stored in `meta_json`, so no column migration).
- **Exit:** baseline numbers committed; trace fields default-null; no test regressed.

### Phase 1 — Foundations that don't change replies
- Manifest entity + YAML loader: `returns`, `max_result_tokens`, `destructive`, `destructive_when`, `prompt_example` (all defaulted → inert). Set values on the 5 existing manifests.
- `AgentContext`: `session_id: str | None = None`, `speaker_id: str = ""`. `StreamRequest.session_id` optional; thread through `stream.py` and `voice_session.py`.
- `ConversationManager.add_turn(..., session_id=None)` so one resolved id is used for turns and state (G5).
- `SessionContextPort` (domain) + `SessionContextRow` model + `SqliteSessionContextRepository` (tpa) + register in `init_tables`; wire in `bootstrap()`. State keyed `(session_id, speaker_id)`, keyed on **tool name** (G1), TTL from `agents.yaml: session_ttl_sec`, lazy expiry.
- **Shadow write:** write validated args after `ok` tool calls in the *existing* ToolAgent path; log the would-be `ACTIVE:` line. No prompt/reply change.
- **Exit:** after "weather in Tokyo" the row exists with `slots={"place":"Tokyo"}`; golden results byte-identical to Phase 0.

### Phase 2 — Control schema + prompt (spike, still not wired)
- Spike on the Pi/llama.cpp: does the grammar accept `oneOf` + nullable string + `needs_live_data` first? Pick field order (G6) and `num_predict` (G7).
- `build_control_schema` (reuse `tool_args_schema` and the `oneOf` builder; never `minItems:1`).
- `config/prompts/control_stage.md` (needs_live_data text amended per G2; 8 pattern examples; pattern 4 mandatory), `PromptComposer.control_stage()` with ONE global `_static_cache` key, `ACTIVE:` line, complete exchanges.
- Unit tests: prefix byte-identity across turns; SYSTEM block contains no date/history/state; prompt size independent of tool count.
- **Exit:** schema + prompt tests green; measured control-prompt tokens recorded.

### Phase 3 — `AssistantOrchestrator` behind the flag
- `orchestrator_enabled`, `session_ttl_sec`, `control_history_exchanges` in `agents.yaml` (+ typed in `core/config.py`).
- `service/agent/assistant_orchestrator.py`: resolve session once → control decode → dispatch table → execute via **existing** `ToolTurnRunner._execute`/`ExecutedCall` + SkillRunner → reply policy (template joined verbatim; content stage only for `returns: document`) → `claims_action` backstop on every path → persist turn + session state + TurnTrace. Parse failure fails closed.
- `SupervisorAgent.execute[_stream]` delegates when the flag is on (ambient path untouched; decide G8).
- Streaming: tool/clarify/refuse paths emit once; chat path streams via ResponderAgent as today.
- **Exit (flag on):** Tokyo→umbrella reuses Tokyo; Singapore case answers "2"; exactly one control decode (check `timings_ms`); `prompt_tokens["control"]` stable. **Flag off:** identical to Phase 2.

### Phase 4 — Capabilities the new path needs
- `get_weather_forecast(place, date_offset 0..6)` + manifest; templates name place and day.
- `SystemAgent` → manifests `app_control`, `volume_control`, `device_status` + `SystemControlPort` skill wrapper (runs through SkillRunner; higher permission for `close`). Decide exposure of terminal/file_ops (Q-A).
- Slot guards: destructive calls never read/write session slots; destructive excluded from multi-call.
- Bound `ResponderAgent._render_history` (per-turn clip + token budget); session-scope `ConversationManager.turns`.
- **Exit:** forecast follow-up works; system actions behave as before but via SkillRunner; responder prompt tokens bounded and logged.

### Phase 5 — Data plane
- `narrate_stage` → `content_stage(task, document, user_message)`; task line in volatile tail; `narrate` + `is_grounded` unchanged.
- Enforce `max_result_tokens` before composition; test that a `returns: document` result can never enter the control prompt.
- **Exit:** one cached prefix for all content tasks; narrate behaviour unchanged.

### Phase 6 — Measure & cut over
- Run the full golden set on the Pi with the flag on vs off; compare to accuracy targets (missed-tool <2 %, wrong-write ≈0, etc.), p50/p95 latency for HTTP and voice, prefix count, cache MB.
- If targets miss: Plan B (fixed tool groups chosen deterministically) — never a second decode/model/regex.
- If chat latency is unacceptable: **ask the user** before the merged-schema or handoff-flag options.
- Flip `orchestrator_enabled: true` by default; soak.
- **Exit:** targets met and recorded.

### Phase 7 — Deletion (only after a soak on the new default)
Delete: `SupervisorAgent._pick`/routing args/`routing_status`/`warm_router`, `routing_policy` matchers, `RouterPolicy`, `LlmRouter`, `route_stage`, `build_route_schema`, `config/routing.yaml` + `cfg.routing`, `router.md`, the Qwen3-0.6B router wiring, `required_when` forcing + `_FORCE_NOTE`, legacy `decide`/`call_stage`/`build_call_schema`, `SystemAgent`, `ResponderAgent._without_action_claims` + `claim_filter`, per-owner `ToolAgent`s. Update/delete `test_llm_routing`, `test_golden_routing`, `test_routing_modes`, `test_router_timeouts`, `test_slow_device`, `scripts/eval_routing.py`. Update `docs/00`–`08`. Confirm exactly 3 cached prefixes.
- **Exit:** grep for `match_agent|required_tools|route_stage|LlmRouter` finds nothing in `src/`.

### Phase 8 — Gmail/Calendar (later; do not start early)
As in the design doc §Phase 7, gated on Q8.

---

## 5. Decisions (answered 2026-10-07)
- **Q-A → decided:** `terminal` / `file_ops` are **not** model-callable in v1. They stay out of the control tool list and out of `config/tools/`.
- **Q-B → decided:** latency should improve but reliability is not negotiable. So: no merged `{calls, reply}` schema, no model-chosen handoff.
  Phase 6 sets a measured budget on the Pi (chat turn ≤ today's chat latency + control decode with a warm prefix). Levers, in order:
  keep the control output tiny (`num_predict` small, `needs_live_data` first), warm the control prefix on boot, drop the 0.6B router (frees CPU/RAM and removes a
  decode that costs more than the control decode will), stream the chat reply as today.
- **Q-D → decided:** scope is **agent orchestration design** only (routing/decision/execution/reply flow). Layering, config and voice are out of scope except where orchestration touches them.
- **Q-C** still open (TTL, tool count, multi-intent) — defaults: 900 s, <30 tools, multi-intent allowed (≤3 independent calls), revisit in Phase 6.

## 6. Phase status
- **Phase 0: done except the two device-only items.**
  - Golden set: `tests/eval/orchestrator_golden.yaml` (41 cases, 14 categories, `phase4:` overrides) + structural test `tests/test_orchestrator_golden.py`.
  - Trace fields added (`state_used, slots_inherited, needs_live_data, clarified, prefix_cache_hit`; stored in `meta_json`, no migration). `fast_path` from the design doc was **dropped** — the design has no fast path.
  - Baseline of today's rules-only routing: `scripts/eval_orchestrator.py --save` → `tests/eval/results/orchestrator-baseline-*.json`: **27/41** route as expected.
    Bug A: 4/6 follow-ups missed (routed to chat). Bug B: 6 non-requests (Singapore, negation, quotation, meta, technical, "no tasks") routed to a tool agent and would hit the forcing regex.
  - Test suite: 520 pass, same 6 pre-existing failures in `test_golden_routing.py` (no regressions).
  - **Still needs the Pi:** model-decode baseline and 3 real traces (`prompt_tokens`, `timings_ms`, router latency) to settle D7 and set the Phase 6 latency budget.

- **Phase 1: done (shadow mode, no reply/route/prompt changed).** Suite: 534 pass, same 6 pre-existing failures; orchestrator baseline unchanged (27/41).
  - Manifest fields `returns`, `max_result_tokens`, `destructive`, `destructive_when`, `prompt_example` — schema-validated (param must exist, values within the enum, ≤1 `prompt_example`). Set on `tasks` (delete), `remember` (forget), `web_search` (`returns: document`, 320 tokens).
  - Domain: `SessionContext` entity, `SessionContextPort`, pure `destructive_policy` and `session_state_policy` (`state_after_call`, `active_line`, TTL/expiry).
  - Adapter: `SessionContextRow` (`session_contexts`, PK `(session_id, speaker_id)`) + `SqliteSessionContextRepository` (upsert, lazy expiry, `purge_expired`); registered in `init_tables`; hourly purge added to the housekeeping loop.
  - Service: `service/session/SessionStateService` (never raises); wired in `server.py::bootstrap`; `ToolAgent` resolves the session id **once** per turn (`ctx.session_id`), writes state after successful calls, and logs the would-be `ACTIVE:` line. `ConversationManager.add_turn(..., session_id=None)` + `current_session_id()` so turns and state share the id (G5).
  - `AgentContext.session_id/speaker_id`, `StreamRequest.session_id` (optional) → `stream.py`; `agents.yaml: session_ttl_sec: 900`.
  - **Deviations from the design doc:** (1) state is keyed on the **tool name**, no `capability` column (G1). (2) The shadow `ACTIVE:` log lives in `ToolAgent`, not `PromptComposer` — the composer renders it for real in Phase 2/3 via `session_state_policy.active_line`. (3) `voice_session.py` is **not** threaded: no component there has a session id to supply; it resolves lazily like before. (4) `chat.py` no longer exists (D3).
  - Nothing reads the state yet; verify on the Pi after "weather in Tokyo": `SELECT * FROM session_contexts;` and the log line `[session-state] shadow ACTIVE: get_weather | place=Tokyo | …` on the next turn.

- **Phase 2: done (schema + prompt + tests); protocol choice pending the spike below.**
  - `build_control_schema` (flag first by default, never `minItems:1`, nullable `clarification`) — verified against the real llama.cpp grammar converter on Qwen3-4B (`oneOf` + `["string","null"]` accepted).
  - `config/prompts/control_stage.md` + `PromptComposer.control_stage()` (one global cached prefix, `ACTIVE`/`RECENT` pairs/`TODAY`/`USER` tail, JSON examples re-serialised to the schema's key order). Budget `control: 1900` (static ≈ 1400 est. tokens).
  - Guard tests: prompt examples must not duplicate golden cases; static prompt must leave ≥400 tokens of headroom.
- **Phase 3 core: built, behind `orchestrator_enabled` (default false).** Suite 600 pass / same 6 pre-existing failures.
  - Pure domain rules: `ControlDecision`, `dispatch_policy.resolve` (TOOLS/CLARIFY/CHAT/REFUSE/FAIL_CLOSED, fails closed, destructive never in a multi-call), `reply_policy` (templates verbatim and never sent to the model; ONE content decode for the rest), `SentenceClaimGuard`.
  - Services: `ControlDecoder` (strict parse; never raises), `AssistantOrchestrator` (one session resolve, one decode, tools via `SkillRunner`, backstop on every path, persists turn + state + trace, streams chat), `Supervisor` delegates when set, `server.py` wiring (router model NOT loaded when on; control prefix warmed on boot). Shared `execute_call` / `narrate_with_grounding` extracted from `ToolTurnRunner` (legacy path unchanged, tests green).
  - `ResponderAgent(reply_veto=...)`: claims backstop for chat, non-streaming and streaming (sentence-gated; model is stopped through a private cancel event). With the orchestrator on, the old `_without_action_claims` history filter is disabled (P13 fixed under the flag).
  - Deviations: governance `route_to_agent` check is not run on the orchestrator path (there is no routing; tool governance still runs in `SkillRunner`). `pending` clarification state is not rendered yet (prose history carries it); measure first.
  - **Spike result on real Qwen3-4B (laptop, 12 threads), old prompt:** A1 28/41, A2 27/41 (key order is not the lever); failures were prompt-quality (example leakage, no per-tool examples, forced tool when none fits) and the missing forecast tool. Prompt rewritten (flagged per-tool examples, "no fitting tool" rule, examples ≠ golden cases); re-measure pending.

## 6b. Alignment with `docs/implementation_guide.md` (added 2026-10-07)
The guide follows the design doc's phases, and the user confirmed those phases are the target. **That supersedes my earlier "delete in Phase 7" ordering (G3):**
the router, forcing regex, `_without_action_claims`, `SystemAgent` and the keyword/LLM routing are deleted in **guide Phase 3**, right after the flag-on checks of Phase 2 pass.

| Guide phase | What it is | Status |
|---|---|---|
| 0 baseline + trace fields | golden set, `TurnTrace` fields | **done** (my golden set is a separate file `orchestrator_golden.yaml`, extended with the guide's cases) |
| 1 session state, shadow | table, port, writer | **done** |
| 2 control decode behind the flag | schema, prompt, composer, orchestrator, flag | **built**; flag-on live gate **in progress** (spike re-run) |
| 2.1b budgets as config | `config/prompting.yaml` | **done** (real defaults copied verbatim; `control` added; `control_history_exchanges` moved here) |
| 3 deletions + `SystemAgent` → 3 manifests | router, forcing, P13, system agent | **next** — only after the Phase 2 live gate |
| 4 forecast + bounds | `get_weather_forecast`, responder budget, session-scoped turns | pending |
| 5 data plane | `content_stage` | pending (manifest fields already in) |
| 6 cutover | flag default on, delete legacy, extract `AmbientDispatcher` | pending |

**Phase 3 prep already in the tree (inert until the cutover step):** `service/skills/builtin/system_control.py` — `AppControlSkill`, `VolumeControlSkill`, `DeviceStatusSkill` (guide §3.5 mapping; spoken text from the port's real return values; volume range checked in code; `system_action` governance check kept; port calls run off the event loop). 14 tests. **Not registered and the three `config/tools/*.yaml` manifests are not added yet**, because an `agent: system` manifest would collide with the live `SystemAgent` registration; both land in the same step that deletes `SystemAgent`.
**Known gap carried over:** `SkillRunner` permission is per skill, not per argument, so `app_control close` cannot be given a higher level than `open` (the design doc asks for this). `SystemAgent` bypassed permissions entirely, so this is no worse than today; per-argument permission is a follow-up.

**Where I deliberately differ from the guide (and why):**
- `_parse` failure: the guide's returns an empty decision, which routes to **free chat** (fail-open, violates P4). Mine returns INVALID → fixed "didn't catch that" reply.
- `needs_live_data` text: the guide lists "my saved facts" as live data → would refuse "what's my favourite sweet?". Removed (review gap G2).
- Session state keyed on **tool name**, not `capability=manifest.agent` (the guide's own rule is self-contradictory: `lookup` → `weather`).
- Prompt examples are not golden strings: the guide's pattern 3/4 reuse test sentences ("what about Delhi?", the Singapore case); in the spike the model copied "Delhi" into a no-state case. A test now forbids prompt examples that duplicate golden cases. Mandatory patterns 3 and 4 are kept in different words.
- `needs_live_data` first in the schema (spike: order made no measurable difference, 28 vs 27 of 41).
- Pure policies (`dispatch_policy`, `reply_policy`, `destructive_policy`, `session_state_policy`, `claim_guard_policy`) live in `domain/`, not inside the orchestrator.

**Stale in the guide (code has moved on):** `routes/chat.py`, `ChatRequest` and `routes/ambient.py` no longer exist (commit `1771944`). `dispatch_ambient` has **no callers** left; `set_proactivity` is still called by `routes/config.py` and `routes/persona.py`, so the Phase 6 `AmbientDispatcher` extraction is still needed for it.

**Spike result (old prompt, n=41, Qwen3-4B, laptop):** A1 constrained 28 · A2 (calls first) 27 · A3 (no `ACTIVE:` line) 28 · **B native `<tool_call>` 26 (10 missed tool calls)**.
The native format is less reliable than the constrained decode, and `ACTIVE:` added nothing measurable on this set because the two-exchange history already carries the context — worth re-testing before keeping session state.

## 6c. PAUSED here (2026-10-07) — Phase 2 live gate: measured, not yet cleared
**State:** nothing committed. Phase 3 deletions NOT started (they are gated on this). Suite: 6 known pre-existing failures in `test_golden_routing.py`, everything else green.
`scripts/spike_decision_protocol.py` now scores the whole pipeline (model output + `dispatch_policy.resolve`); `--rejudge <json>` re-scores saved model outputs offline, so golden/judge/policy changes cost no model time.

**Protocol comparison (Qwen3-4B, laptop, 41 cases, raw model output):** A constrained control decode 28 · native `<tool_call>` (single-agent design) 26, 10 missed tool calls · hybrid grammar 18 (44%, emitted three deletes for "delete it"). **Constrained control decode (A) is the one to keep.**

**Pipeline view on 51 cases (this is what the user would experience):**
| Variant | Score | Note |
|---|---|---|
| A1 (old prompt + structural fixes) | 37/51 (73%) | destructive 3/3 — the target veto turns guessed deletes into questions |
| A1 + explicit `ACTIVE: none` / `RECENT: none` | 39/51 (76%) | within noise of the above (n=51) |
| **A5 = A1 + temperature 0** | **41/51 (80%)** | best; destructive 3/3, no wrong write |
| A6 = A1 + `intent` field first | 33/51 (65%), 10 invalid | **rejected** — worse, and truncates at `num_predict=200` |

**Remaining A5 failures (10):** 2 need the Phase 4 forecast tool ("and tomorrow?", "will it rain tomorrow in Pune"); 2 are `remember list` calls for "what do you remember / what is my favourite food" (harmless read; chat could answer from saved facts);
6 are model weaknesses: a quote and a "don't tell me the weather" negation still call `get_weather`, "capital of Japan" goes to `web_search`, two no-context follow-ups ("what about tomorrow?", "and in Delhi?") guess a place, and one multi-intent drops the place.
This set is adversarial by construction, so 80% overstates the error rate on ordinary speech; the design targets (<2% missed, <3% false calls) are NOT demonstrated and need a held-out set and the Pi.

**When resuming (not yet done):**
1. Wire the winners into production code: `ControlDecoder` temperature `0.0` (currently `0.1`) and `show_empty=True` (the composer option exists, default off; the decoder does not pass it yet). Re-run A5 once on the real decoder path to confirm.
2. Write a held-out test set (fresh phrasings, never used while tuning prompts) before claiming accuracy; consider 2–3 repeats at temperature 0.
3. Then decide with the user whether 80%-on-adversarial clears the gate, or whether to try: a larger model for the control decode, fixed tool groups (Plan B), or accepting the residual false-call risk (low harm class).
4. Phase 3 per `implementation_guide.md`: register the three system skills + add their manifests, delete `SystemAgent`, routing, forcing, `_without_action_claims`; update/delete the routing tests. Rollback after that is a git revert.
5. Pi: the small-model profile (`n_ctx: 2048`) cannot hold the ~2,000-token control prompt; it needs its own `prompting.budgets` override or a larger context. Measure real prefill/decode latency and cache hit on the device.
6. Judgment call for review: `destructive_policy.target_is_explicit` reads the user message (as a fail-closed veto on a destructive call only, never to pick or force a tool).

## 7. Open questions (need your answer)
- **Q-A** Should `terminal` / `file_ops` become model-callable tools at all in v1? (My recommendation: no — keep them out of the control tool list until approval UX is proven.)
- **Q-B** Are you OK with chat turns costing 2 decodes if Pi measurements show ≤ ~3 s extra? Otherwise we need the merged-schema trade-off (loses persona/RAG/streaming).
- **Q-C** Session TTL 900 s, and shorter for voice? Tool count in 12 months (<30 → Plan A)? Multi-intent in v1? (design doc Q3, Q6, Q7)
- **Q-D** Is the routing/orchestration rewrite the whole scope of "issues with my architecture", or are there other pain points (layering, config, voice) you want folded in?

## 6d. Held-out gate result and Phase 4 (2026-10-07, second device)
**Held-out run (Qwen3-4B, laptop, A5 = temperature 0, `show_empty`), 40 cases x 2 repeats, `tests/eval/orchestrator_heldout.yaml`:** **52/80 = 65%** (tuned set: 80%). Both repeats were identical (deterministic), so this is not noise: the tuned score overfit. No invalid output. Saved: `tests/eval/results/spike-protocol-20261007-230443.json`.
By category: destructive 6/6, explicit 18/18, mention 4/4, negation 4/4, slot-guard 4/4, followup 8/10 · general 6/12, meta 0/2, technical 2/4, no-tool 0/4, no-state 0/6, multi 0/4, pending 0/2.
Failure classes: (1) general knowledge sent to `web_search` ("who wrote the ramayana", "how far is the moon", "what does humidity measure"); (2) private data with no tool mapped onto `tasks` ("do i have a meeting", "read my messages"); (3) no-state follow-ups guess instead of clarifying ("how about in euros" -> 100 USD to EUR; "mark it as done" -> task titled `/no_think`); (4) multi-intent emits only the first call; (5) "the first one" answering a pending clarification deletes a task titled "first one" (the destructive veto passed because those words are in the message).
Latency: decision median 13.3 s, p95 22 s on the laptop (decode-bound) — a bigger problem for the Pi than accuracy.
**Bug found:** `llama_cpp_client` appended ` /no_think` to the same line as the user's words, so the model copied it into arguments. Now on its own line (client and spike script). Re-measure: this alone may change some no-state cases.
**Decision (made by the implementing agent, delegated by the user): Phase 3 is HELD.** The new path is not ready to replace the old one; deleting it makes rollback a git revert. Do not tune prompts against the held-out file (burned cases must be replaced with fresh phrasings).
**Phase 4 done (additive, valid on both paths):** `get_weather_forecast` (provider `tpa/online/providers/forecast.py`, `WeatherLookup.forecast`, `GetWeatherForecastSkill`, `config/tools/weather_forecast.yaml`; `date_offset` 0..6 validated in code, spoken reply names place and day; legacy triggers for umbrella/raincoat/tomorrow-weather, deliberately no `required_when`); chat history clipped per turn and trimmed oldest-first to `PromptBudgets.chat` (measured on the assembled prompt, logged); `ConversationManager.turns` scoped to the current session (`recent_turns(session_id=...)`, `turns_in`). The spike script now applies `phase4:` expectations when the tool exists. Destructive/no-slot-inheritance guards were already built (Phase 3 core).
Tests: 633 pass, 12 fail = 5 known `test_golden_routing` + 7 that only fail on a machine without llama_cpp/pyttsx3/piper (`test_router_timeouts`, `test_tts_selection`).

## 6e. Phase 5 — data plane (done)
- `PromptComposer.content_stage(task, document, user_message, *, max_tokens=0)`, tasks `narrate | summarise | draft_reply | extract` (`CONTENT_TASKS`; instruction files `narrate.md`, `content_summarise.md`, `content_draft_reply.md`, `content_extract.md`). SYSTEM = persona_lite + `content_stage.md`, identical for every task (ONE cached prefix); the `TASK:` instruction is the first line of the volatile tail. No tool list, no history.
- `narrate_stage(q, results)` is now a wrapper over the `narrate` task, so the legacy path and `narrate_with_grounding` / `is_grounded` are unchanged. Change to be aware of: the narrate instruction moved from the system block to the prompt tail (required for the shared prefix), so the first narrate after boot prefills ~70 tokens more; document label stays `TOOL RESULT`.
- The producing tool's `max_result_tokens` is applied first (`max_tokens`), then the existing stage caps (`observation_max`, `narrate` budget) still apply.
- Found and closed a leak: when narration of a `returns: document` tool failed, the fallback spoke (and saved to history) the observation's first line, which for an email body is the whole document. It is now capped to 60 tokens (`_DOCUMENT_FALLBACK_TOKENS`). Tests: the control prompt has no tool-result input at all (signature test), a document can't reach history or `ACTIVE:`, and the content decode is reached with the `narrate` task.
- Nothing in Phase 5 uses `summarise`/`draft_reply`/`extract` yet: they wait for Gmail/Calendar tools (guide Phase 7). Unmeasured on a model.

## 6f. Evals after the `/no_think` fix + forecast tool; AmbientDispatcher extracted (2026-10-08)
**Tuned golden set, A5, 51 cases: 43/51 = 84%** (was 80%). Saved `tests/eval/results/spike-protocol-20261007-234010.json`. followup 9/9, no-tool 4/4, destructive 3/3. Remaining 8: missed `remember save` ("my favourite sweet is gulab jamun" -> no call), 3 false `get_weather` calls (negation, quote, technical), "capital of Japan" -> web_search, 2 no-state follow-ups guessing ("what about tomorrow?" now picks the forecast tool with no place, "and in Delhi?"), 1 multi-intent dropping the second call. Decision latency median 13 s, p95 24 s (laptop). The held-out run was still in progress when this was written.
**AmbientDispatcher** (guide Phase 6, first step; behaviour unchanged): `service/sensing/ambient_dispatcher.py` now owns `dispatch_ambient`, proactivity, debounce and rate limit. `SupervisorAgent` no longer has any of it. `routes/config.py` and `routes/persona.py` use the new `get_ambient_dispatcher` provider; `server.py` builds it and exposes `app.state.ambient_dispatcher`. `dispatch_ambient` still has no caller (as before). 9 new tests (it had none).

## 6g. Phase 3 — deletions DONE (2026-10-08; requested by the user before the held-out result arrived)
**Rollback is now `git revert` of the Phase 3 commit; there is no flag.** The user chose to proceed while the held-out run was still going; the gate (held-out ≈ tuned 84%) is therefore NOT cleared at the time of this commit.
Deleted: keyword routing (`routing_policy.py`, `RouterPolicy`), the router model (`LlmRouter`, `RouterPort`, `route_stage`, `build_route_schema`, `router.md`, `config/routing.yaml`, `cfg.routing`, `_build_router`, the Qwen3-0.6B wiring, `routing_status`/`warm_router`, the `/health` routing block), the `required_when` forcing (`_FORCE_NOTE`, the retry, `ToolUseGuard.required_tools`), `ResponderAgent._without_action_claims` + `claim_filter` (P13), `SystemAgent`, the flags `orchestrator_enabled` / `llm_routing` / `tools_enabled`, the `route` budget, the `governance route_to_agent` check (nothing routes any more; per-tool governance runs in SkillRunner).
Replaced: `SupervisorAgent(orchestrator, model)` only delegates. `app_control`, `volume_control`, `device_status` are manifests + the three skills registered in `server.py` (the port calls and spoken text are what `SystemAgent` did; the `system_action` governance check is kept). `ToolAgent`/`ToolTurnRunner` are no longer constructed (dead; deleted with their tests in the cutover step, per the guide) but the forcing block is already gone from `ToolTurnRunner`.
Also deleted (they only measured the removed routing): `scripts/eval_tools.py`, `scripts/eval_orchestrator.py`, `scripts/eval_routing.py`, `tests/eval/golden_set.yaml`, `test_golden_routing`, `test_llm_routing`, `test_routing_modes`, `test_router_timeouts`, `test_slow_device`. `scripts/spike_decision_protocol.py` is the eval.
Changed: `control` prompt budget 2600 -> 3000 (the four new tools carry prompt examples; static prompt is now ~2340 est. tokens, so the tail still has >= 400); a small-profile `n_ctx: 2048` still cannot hold it. Destructive-target veto: a pronoun/placeholder (`it`, `that`, `them`, `one`...) never counts as the user naming a target, and 1-2 char targets need a whole-word match — the smoke test showed `close it` -> `app_control(name="it")` would have run.
Verified by booting the real `bootstrap()` with a fake model: all 11 skills register, agents = {responder, supervisor}, open/volume/close/chat/refuse turns work, "close it" now asks "Which one do you mean?". Suite: 411 pass, 5 fail = `test_tts_selection` (no pyttsx3/piper on that machine).
Left for the cutover step: delete `ToolAgent`, `ToolTurnRunner` decide path, `call_stage`, `build_call_schema`, `budgets.call`; the dead manifest fields `triggers` / `required_when` (kept for Plan B / no reader now); update `docs/00`-`08` and the README.

## 6h. Held-out re-run (after `/no_think` fix + forecast tool + Phase 3) and cutover cleanup (2026-10-08)
**Tuned golden set 43/51 = 84%; held-out set 25/40 = 62%** (`tests/eval/results/spike-protocol-20261007-235627.json`). The 22-point gap is overfitting, not noise or the earlier bugs. **The accuracy gate is NOT cleared** and Phase 3 had already removed the old path at the user's request, so this is the state of the product, not a pending decision.
Judge strictness: `is it going to be cold in Manali` and `should i carry a jacket` (current weather instead of the forecast tool) count as WRONG_CALL; accepting them gives ~27/40 = 68%. Real misses: (1) general knowledge -> `web_search` (ramayana, moon) and a technical question -> `get_weather`; (2) private data with no tool mapped onto `tasks` (`do i have a meeting`, `read my messages`), which would answer from the wrong data; (3) no-context follow-ups guess (`how about in euros` invents 100 USD, `and what about the weekend` calls the forecast with no place, `mark it as done` completes a task titled with the whole sentence); (4) two-intent messages drop the second call (0/4 across both sets: systematic, not luck); (5) `the first one` after a clarification produced `tasks delete title="first one"`.
Good: mention-without-request 4/4, negation 4/4 on held-out (Bug B), destructive 3/3 and slot-guard 2/2; follow-ups 3/5 (both misses are the judge/forecast choice).
**Latency is the bigger problem**: decision median 14.6 s, p95 24 s. It is decode-bound (~0.35 s per output token; a chat turn's `{"needs_live_data":false,"calls":[],"clarification":null}` is ~22 tokens = ~8 s, a tool call ~35-40 tokens = ~13 s). On the Pi this is worse. Cheapest lever, not yet measured: shorter output (drop the always-`null` `clarification` key, shorter key names) — about 8-10 tokens = ~3 s.
**Policy change made from this run** (verified offline with `--rejudge`, scores unchanged, no regression): the destructive-target veto now treats ordinals and filler words (`first`, `last`, `one`, `the`, `task`...) as naming nothing, so `the first one` -> "Which one do you mean?" instead of a delete attempt. A numeric-argument grounding veto was rejected: voice input says "five hundred", so a digit check would reject correct calls.
**Cutover cleanup done (dead since Phase 3):** `ToolAgent`, `ToolTurnRunner` (the helpers live on in `service/agent/tool_execution.py`), `call_stage`/`_static_call_text`, `build_call_schema`, `config/prompts/call_stage.md`, `budgets.call` / `call_history_turns`, `tests/test_tool_turn.py` and the ToolAgent tests in `test_session_state` / `test_traces_and_grounding`. `docs/00`, `01`, `03`, `04`, `06`, `08` rewritten to match. Left: the manifest fields `triggers` / `required_when` have no reader (kept for a possible Plan B grouping); `tpa/governance/policies/agent_routing.yaml` governs the deleted `route_to_agent` action.
**Options for the decision quality (user's call; none tried):** (a) shorter control output (latency only); (b) fixed tool groups chosen deterministically (Plan B) to shrink the menu the 4B model sees; (c) a larger control model (more accurate, slower: not viable on the Pi); (d) accept the residual false-call rate (most are harmless reads) and fix only the harmful classes (private-data -> `tasks`, no-context guesses). Do not tune the prompt against `orchestrator_heldout.yaml`.
