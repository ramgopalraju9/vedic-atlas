# HANDOFF — unified control decode (current state, 2026-10-08)

**Branch:** `feature/unified-orchestrator` (from `release/v1.1` @ `1771944`). Not merged. Read `docs/10-orchestrator-review-and-plan.md` §6d-§6i for the measurements and decisions.

## What exists now
Every user turn goes `SupervisorAgent` -> `AssistantOrchestrator`: ONE grammar-constrained control decode (`{needs_live_data, calls[<=3]}` + optional `clarification`) -> `dispatch_policy` -> tools through `SkillRunner` -> template replies verbatim (no second decode), or one content decode, or the chat `ResponderAgent`. There is **no routing, no router model, no keyword/`required_when` forcing, no `SystemAgent`, no `ToolAgent`, and no feature flag**: rollback is `git revert` of the Phase 3 commit (`9d53bb6`). Design: `docs/unified-assistant-orchestrator.md`; step list: `docs/implementation_guide.md`; architecture docs `docs/00`-`08` are updated to this state.

| Piece | Status |
|---|---|
| Phases 0-6 of the guide | done (see below for what is NOT proven) |
| Tools | `get_weather`, `get_weather_forecast`, `convert_currency`, `web_search`, `tasks`, `remember`, `app_control`, `volume_control`, `device_status` (terminal/file_ops are NOT model-callable, by decision) |
| Data plane | `PromptComposer.content_stage(task, document, ...)`, one shared prefix; a `returns: document` result can never reach a control prompt |
| Session state | `session_contexts` table, keyed by tool name, TTL 900 s, never from a destructive call |
| Safety | destructive call must have its target named by the user (pronouns/ordinals/filler don't count); claims backstop on every reply path; unparseable decision fails closed |
| Tests | `pytest -q`: 392 pass; 5 `test_tts_selection` failures only on a machine without pyttsx3/piper |

## What is NOT done / not proven (the real remaining work)
1. **Accuracy gate not cleared.** Qwen3-4B Q4, temp 0: tuned golden set 84% (43/51), held-out set 62% (25/40). Misses: trivia sent to `web_search`; private data ("my meetings", "my messages") mapped onto `tasks`; no-context follow-ups guess a topic/amount; two-intent messages drop the second call (0/4); `the first one` after a clarification (now vetoed -> asks). Options: larger control model (not viable on the Pi), accept/mitigate false calls (most are harmless reads), fix the harmful classes. **Never tune prompts against `tests/eval/orchestrator_heldout.yaml`**; replace burned cases with fresh phrasings.
2. **Latency.** Decision median ~14.6 s / p95 ~24 s on the dev laptop, decode-bound (~0.35 s per output token). The last change (optional `clarification`, `30b82a2`) should save ~2 s and is **unmeasured**; verify llama.cpp accepts the optional key, then re-run both evals and compare with 43/51, 25/40 and the median.
3. **Pi:** nothing measured on the device (latency, prefix-cache hits, `prompt_cache_mb`). Small profiles now use `n_ctx: 3072` + `prompting.budgets.control: 2800` (a test enforces they fit); quality of 0.6B/1.7B on this prompt is unknown.
4. Known gaps: `SkillRunner` permission is per skill, not per argument (`app_control close` cannot need more than `open`); the `pending` clarification state is stored in the table but not rendered (prose history carries it; not needed so far); manifest fields `triggers`/`required_when` have no reader (kept for a possible Plan B); `tpa/governance/policies/agent_routing.yaml` governs the deleted `route_to_agent` action; Gmail/Calendar (guide Phase 7) is intentionally not started.

## How to run things
```bash
pytest -q                                   # no model needed; includes an isolated bootstrap smoke test
python scripts/spike_decision_protocol.py --variants A5                                          # tuned set, REAL model
python scripts/spike_decision_protocol.py --variants A5 --golden tests/eval/orchestrator_heldout.yaml
python scripts/spike_decision_protocol.py --rejudge tests/eval/results/<run>.json [--golden ...] [--phase4]   # re-score saved output, no model
```
Needs `data/Qwen3-4B-Q4_K_M.gguf` (gitignored; path in `config/inference.yaml`) and `llama-cpp-python`. The venv is not in git: `python -m venv .venv && pip install -e .`.

## Rules for the next person
- **Never `rm -rf` the gitignored `data/` (models, `veda.db`, saved facts).** An earlier session did, after a smoke test, and lost the user's DB and saved facts (models were restored from copies). Use `tests/test_bootstrap_smoke.py`'s approach (a temp copy of `src/` + `config/`) for anything that boots the app.
- Layering `controller -> service -> domain <- tpa`; `domain/` pure; concrete adapters only in `src/server.py`; errors via `AppException` + `ExceptionCode` (add a row in `exceptions/handlers.py::_STATUS_BY_CODE`).
- The Bash tool here eats backslashes in heredocs/`python - <<EOF` (`\n` becomes a newline). Write scripts with the Write tool and run them as files; use Edit for anything containing regexes or `\n`.
- `docs/` and `data/` are gitignored; docs here were force-added (`git add -f docs/...`).
- Do not commit: `.idea/`, `vedic-atlas-env/`, `wakeword_training/`, `src/*.egg-info/`, `tests/eval/results/routing-v1-prompt-*.json`.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
