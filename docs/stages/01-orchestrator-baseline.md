# Stage 1 — Unified control decode (the baseline everything else is measured against)

*Oct 7–8 2026. Sources: `docs/session-log.md`, `docs/HANDOFF.md`, `docs/10-orchestrator-review-and-plan.md`.*

## Goal
Replace the old chain (keyword router → 0.6B router → per-agent tool decision → planner) with **one grammar-constrained decode per turn**:
`{"needs_live_data", "calls":[≤3], "clarification"?}`, then a pure `dispatch_policy` decides TOOLS / CLARIFY / REFUSE / CHAT.

## What we built
- Phase 0–3 of the guide: golden set, session state (`ACTIVE:` line), control schema + `control_stage.md`, pure policies, `ControlDecoder`, `AssistantOrchestrator`, reply veto.
- Safety: a destructive call must have its target named in the user's own words (a pronoun or "the first one" never counts). This came from a real failure — the model invented `task_id: 1` for "delete it".

## Metrics (Qwen3-4B Q4, temperature 0 unless noted)
| Measurement | Result |
|---|---|
| Protocol: constrained decode / native `<tool_call>` / hybrid grammar (41 cases) | 28 / 26 / 18 |
| Rules-only routing (old design) | 27/41; follow-ups missed 4/6; 6 false forced calls |
| Pipeline score, first version → explicit `none` markers → temp 0 | 73% → 76% → **80%** |
| An `intent` field first | made it worse (65%) |
| Tuned golden set | 43/51 (84%) |
| Held-out set | 28/40 (70%) |
| Decision latency on the dev laptop | median ~14.6 s, p95 ~24 s (decode-bound, ~0.35 s per output token) |

## Where it failed
Trivia sent to `web_search`; private data ("my meetings") mapped onto `tasks`; follow-ups with no context guessed a topic; two-intent messages dropped the second call (0/4).

## Decision / why we moved on
The design was kept (it beat the alternatives by measurement, not argument). The accuracy gate (≥ the targets in `docs/10`) was **not** cleared, and latency was far too slow for voice on a 4B model. Both facts later pointed to a smaller, fine-tuned model (Stage 6–9).
Rule set here: never tune prompts against `orchestrator_heldout.yaml`.
