# Session log — unified control decode (2026-10-07)

A readable export of the working session, for a Claude (or person) picking this up on another device. It is a curated account, not the raw transcript: the raw transcript is a ~4 MB JSONL of tool output that is not useful to resume from. The authoritative state is `docs/HANDOFF.md`; the measurements are in `docs/10-orchestrator-review-and-plan.md`.

## 1. The request
The user had "lots of issues" with the agent orchestration design and wanted: review another agent's design (`docs/unified-assistant-orchestrator.md`), understand the current and target architecture, write notes, then an implementation plan and phase-by-phase implementation. Later they added `docs/implementation_guide.md` (same phases, more detail) and said the phases do not deviate.

## 2. Review (what I found)
Current: keyword router → 0.6B router model (hybrid mode, on by default) → per-agent tool decision → separate `SystemAgent` planner. Bug A: follow-ups ("should I bring an umbrella?") lose context because the router sees only the newest message. Bug B: a `required_when` regex forces a tool the model correctly refused.
Verdict: diagnosis and destination right; the doc was stale and had gaps. Drift: D1 a 0.6B router already shipped; D2 more routing artefacts to delete; D3 `chat.py`/`ChatRequest` no longer exist; D4 a referenced doc missing; D6 `terminal`/`file_ops` manifests don't exist; D7 router latency measurements disagree (2.4 s vs 30 s+). Gaps: G1 `capability` undefined; G2 `needs_live_data` contradicts saved facts; G3 phase-order bugs; G5 session id resolved twice; G6 schema key order; G7 `num_predict`; G8 governance hook; G9 eval format; G10 layering conventions. Test baseline then: 514 pass, 6 fail (pre-existing).

## 3. Decisions the user made
Terminal/file tools not model-callable. Latency matters but reliability is not negotiable. Scope = orchestration only. Later: Veda is voice-first, chat is only a test feature, so quote cases don't matter.
A second, competing design existed (`data/single-agent-design.md`: one agent, native `<tool_call>`, LangChain, chat in one call). It was measured, not argued; see below. Copies are in `docs/reference/`.

## 4. What was built, in order
- **Phase 0:** `tests/eval/orchestrator_golden.yaml` (now 51 cases), `TurnTrace` fields, `scripts/eval_orchestrator.py` baseline (rules-only routing matched 27/41; Bug A 4/6 follow-ups missed, Bug B 6 false forcing cases).
- **Phase 1:** manifest fields, `AgentContext.session_id/speaker_id`, `session_contexts` table + repo + `SessionStateService`, shadow write and `ACTIVE:` log in `ToolAgent`; session id resolved once per turn.
- **Phase 2:** `build_control_schema`, `control_stage.md`, `PromptComposer.control_stage` (one cached prefix), `config/prompting.yaml` budgets.
- **Phase 3 core (not the deletions):** pure policies, `ControlDecoder`, `AssistantOrchestrator`, `Supervisor` delegation, `ResponderAgent` reply veto (streaming-safe), flag + wiring in `server.py`, three system skills (not registered).

## 5. Measurements (real Qwen3-4B on the laptop)
Protocol: constrained decode 28/41 · native `<tool_call>` 26/41 (10 missed) · hybrid grammar 18/41 (three deletes for "delete it"). Prompt rewrite alone did not move 28/41. The failure that mattered most: the model invented `task_id: 1` for "delete it" (it never sees ids), so the model can no longer emit `task_id`, and a destructive call must name its target in the user's own message (veto → "Which one do you mean?").
Pipeline scoring (51 cases): 73% → 76% (explicit `none` markers) → **80% at temperature 0**; an `intent` field first made it worse (65%). Destructive 3/3. The spike earlier judged raw model output and missed that the veto already prevents the wrong writes; `--rejudge` fixed that offline.

## 6. Bugs found and fixed along the way
Streaming claim guard double-saved the turn when the base stream finished first (caught by a test); a prompt example duplicated a golden case ("delete it") and another leaked "Delhi" into a no-state case (now a test forbids duplicates); `llm_routing: true` could still build a router when the orchestrator was on; an assertion had enshrined the `task_id` hazard; the first spike run lost its results to a crash (results now saved incrementally).

## 7. Where it stopped, and why
Phase 2's live gate is measured but not cleared: 80% on an adversarial set that was tuned against; the design targets are not demonstrated. The user asked to pause here and to commit/push everything so another device can resume. Next steps are the resume checklist in `docs/HANDOFF.md` §6.

## 8. Where the raw transcript is
On the original device: `~/.claude/projects/<project-dir>/770ccabb-275d-4570-914d-9cd0089945bc.jsonl` (about 3.8 MB). It was deliberately not committed (local paths, the user's email in system reminders, bulk tool output). Add it by hand if wanted.
