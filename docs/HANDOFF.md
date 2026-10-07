# HANDOFF — unified control decode (read this first on another device)

**Branch:** `feature/unified-orchestrator` (branched from `release/v1.1` @ `1771944`). Work is **paused mid-gate** on purpose.
**Written:** 2026-10-07. Nothing here is merged; the new path is **off by default** (`orchestrator_enabled: false`), so behaviour on the default config is unchanged.

## 0. What this work is
Veda (a **voice** assistant; typed chat is only a test/future feature) decides each turn in up to four places today: keyword router → 0.6B router model → per-agent tool decision → `SystemAgent` planner. The router cannot see conversation, so follow-ups fail (Bug A), and a `required_when` regex forces tool calls the model correctly refused (Bug B).
Target: **one grammar-constrained control decode per turn** (`AssistantOrchestrator`), then execute through `SkillRunner`. Authoritative design: `docs/unified-assistant-orchestrator.md`. Step-by-step: `docs/implementation_guide.md`. My review, decisions and measurements: `docs/10-orchestrator-review-and-plan.md` (**its §6c "PAUSED here" is the resume point**). A readable log of the session: `docs/session-log.md`.

## 1. State in one table
| Guide phase | Status |
|---|---|
| 0 baseline + trace fields + golden set | done |
| 1 session state (shadow) | done |
| 2 control decode behind the flag | built; **live gate measured, not cleared** (see §3) |
| 2.1b budgets as config (`config/prompting.yaml`) | done |
| 3 deletions + `SystemAgent` → manifests | **NOT started** (gated; rollback after it is a git revert) |
| 4 forecast tool, responder bounds, session-scoped turns | pending |
| 5 data plane (`content_stage`) | pending |
| 6 cutover, delete legacy, extract `AmbientDispatcher` | pending |

Tests: `pytest -q` → **623 passed, 1 skipped, 6 failed**. The 6 failures are **pre-existing** in `tests/test_golden_routing.py` (keyword-router cases such as "do I need an umbrella today"); they disappear when the router is deleted in Phase 3. Anything else failing is a regression.

## 2. How to run things
```bash
pytest -q                                                   # whole suite (no model needed)
python scripts/eval_orchestrator.py                         # today's rules-only routing vs the orchestrator golden set
python scripts/spike_decision_protocol.py --variants A1,A5 --repeats 1   # REAL model; ~6 s per case on a laptop
python scripts/spike_decision_protocol.py --rejudge tests/eval/results/spike-protocol-20261007-210826.json   # re-score saved outputs, no model
```
Needs `data/Qwen3-4B-Q4_K_M.gguf` (gitignored — copy/download it; path in `config/inference.yaml`) and `llama-cpp-python`. The virtualenv is **not** in git; create your own and `pip install -e .`.

## 3. Measured results (Qwen3-4B Q4, CPU) — the numbers behind the pause
Protocol choice (41 cases, raw model output): **constrained control decode 28**, native `<tool_call>` (the alternative in `docs/reference/single-agent-design.md`) 26 with 10 missed tool calls, hybrid grammar 18 (and it emitted three deletes for "delete it"). **Keep the constrained control decode.**
Full pipeline (model + `dispatch_policy.resolve`), 51 cases: A1 73% → +explicit `ACTIVE: none/RECENT: none` 76% → **+temperature 0 = 80% (best)**; `intent`-field variant 65% (rejected). Destructive cases 3/3 (no guessed target ever runs).
The set is **adversarial by construction and was tuned against**; 80% is NOT evidence the design targets (<2% missed, <3% false calls) are met. Latency: ~6 s median per decision on a laptop, decode-bound.

## 4. Decisions already made (do not re-ask)
- `terminal` / `file_ops` are **not** model-callable in v1.
- Latency should improve but **reliability is not negotiable** → no merged `{calls, reply}` schema, no model-chosen handoff. Chat turns cost 2 decodes (accepted); measure on the Pi.
- Scope = agent-orchestration design only.
- Veda is **voice-first**: ignore quote/typography failures (e.g. `she asked 'how hot is it'`); STT text has none. Negation / mention-without-request ("I didn't ask about Singapore weather, what's 1+1?") stays in scope.
- Phase order follows the guide: routing/forcing/`SystemAgent` are deleted in Phase 3 (my earlier "delete in Phase 7" idea was dropped).

## 5. Deliberate deviations from the design/guide (each justified in docs/10 §6b)
- Unparseable decode **fails closed** (fixed reply), the guide's fails open to free chat.
- `needs_live_data` text does not list "my saved facts" (chat already has them in its prompt).
- Session state is keyed on **tool name**, not `capability`.
- Prompt examples must not duplicate golden test cases (enforced by a test).
- **Judgment call to review:** `destructive_policy.target_is_explicit` reads the user's message, only as a fail-closed veto on a destructive call (delete/forget must name its target). The model can no longer emit `task_id` (it invented `task_id: 1` and would delete your first task — this hazard exists on the legacy path).

## 6. Resume checklist (in order)
1. Wire the winners into production: `ControlDecoder` temperature `0.0` (currently `0.1`) and `show_empty=True` (composer option exists, default off, decoder doesn't pass it). Re-run `--variants A5` through the real decoder path.
2. Write a **held-out** test set (fresh phrasings never used while tuning prompts; spoken/STT-style); 2–3 repeats at temperature 0.
3. Ask the user whether the result clears the gate, or try: bigger control model, fixed tool groups (Plan B), accept residual false-call risk (low harm class).
4. Phase 3 per `docs/implementation_guide.md`: register `AppControlSkill`/`VolumeControlSkill`/`DeviceStatusSkill` (already written in `src/service/skills/builtin/system_control.py`, 14 tests, **not registered**) and add the three manifests `app_control.yaml`, `volume_control.yaml`, `device_status.yaml` (YAML in the guide §3.5; `destructive_when: {action: [close]}`, `target_params: [name]` on `app_control`); delete `SystemAgent`, `RouterPolicy`, `LlmRouter`, `routing_policy.py`, `routing.yaml`, `required_when` forcing, `_without_action_claims`; update/delete routing tests. Registering an `agent: system` manifest while `SystemAgent` exists would collide — do both in one step.
5. Then Phases 4–6. `dispatch_ambient` has no callers left; `set_proactivity` is still used by `routes/config.py` and `routes/persona.py` (needed for the Phase 6 `AmbientDispatcher` extraction).
6. Pi: the small-model profile (`n_ctx: 2048`) cannot hold the ~2,000-token control prompt → needs its own `prompting.budgets` override. Measure real latency and prefix-cache hits on the device.
7. Known gap: `SkillRunner` permission is per skill, not per argument, so `app_control close` cannot be higher than `open` (same as today; `SystemAgent` bypassed permissions).

## 7. What exists in the code now (all behind `orchestrator_enabled`, default false)
- **Domain (pure):** `ControlDecision`, `SessionContext`; policies `dispatch_policy` (TOOLS/CLARIFY/CHAT/REFUSE/FAIL_CLOSED), `reply_policy`, `destructive_policy`, `session_state_policy`, `claim_guard_policy`; `SessionContextPort`; `build_control_schema`; manifest fields `returns`, `max_result_tokens`, `destructive`, `destructive_when`, `target_params`, `prompt_example`.
- **Service:** `AssistantOrchestrator`, `ControlDecoder`, `SessionStateService`, `SupervisorAgent(orchestrator=…)` delegation, `ResponderAgent(reply_veto=…)` (claims backstop incl. streaming), shared `execute_call`/`narrate_with_grounding` extracted from `ToolTurnRunner`, `PromptComposer.control_stage`.
- **Adapters/config:** `session_contexts` table + `SqliteSessionContextRepository`; `config/prompting.yaml` (budgets), `config/prompts/control_stage.md`, `agents.yaml: orchestrator_enabled, session_ttl_sec`; `TurnTrace` fields (`state_used, slots_inherited, needs_live_data, clarified, prefix_cache_hit`).
- **Tests added:** `test_session_state`, `test_control_stage`, `test_dispatch_policy`, `test_assistant_orchestrator`, `test_system_control_skills`, `test_orchestrator_golden`; golden set `tests/eval/orchestrator_golden.yaml`; evidence in `tests/eval/results/`.

## 8. Conventions to follow (from the project's standing rules)
Layering `controller → service → domain ← tpa`, `domain/` pure (no I/O); `service/` imports only `domain.ports.*`; concrete adapters only in `src/server.py`. Raise `AppException` with an `ExceptionCode` (add a row to `exceptions/handlers.py::_STATUS_BY_CODE` for new codes). Ports-and-adapters for I/O; constructor injection; reuse existing shapes. Read `docs/01`–`06` for the pattern before adding code.

## 9. Gotchas
- **Shell:** the Bash tool in this environment **eats backslashes in heredocs / `python - <<EOF`** (`\n` becomes a real newline). Use the Write/Edit tools for any file containing regexes or `\n`.
- `docs/` and `data/` are in `.gitignore`; docs here were **force-added** (`git add -f`). Do the same for new docs.
- Do not commit: `.idea/`, `vedic-atlas-env/`, `wakeword_training/`, `src/vedic_atlas.egg-info/`, `tests/eval/results/routing-v1-prompt-*.json` (pre-existing untracked, not part of this work).
- Commit trailer used in this repo: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
