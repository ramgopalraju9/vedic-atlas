# Stage 9 — Evaluation on an unseen test set

*Oct 9 2026. Set: `tests/eval/orchestrator_clean.yaml`. Harness: `scripts/spike_decision_protocol.py` (variant A1, the real serving path: model + `dispatch_policy`).*

## The test set
450 hard cases were written by an independent Opus agent that never saw the training data (spoken English, hesitations, self-corrections, homophones, near-miss pairs), validated with the same checker. 104 were removed because they were identical or near-identical in meaning (cos ≥ 0.93) to training sentences → **346 cases**: 203 call · 83 chat · 33 refuse · 27 clarify; 90 carry state/history. Only structured arguments are scored. Frozen: no training or prompt tuning against it. Caveat: one labeler only (about ten borderline cases flagged by its author, not reviewed).

## Results (Qwen3-1.7B)
| Model | Correct | % | call | chat | refuse | clarify |
|---|---|---|---|---|---|---|
| Base, untuned | 131/346 | 37.9% | 120/203 | **0/83** | **0/33** | 11/27 |
| Tuned Q4_K_M | 273/346 | 78.9% | 163/203 | 81/83 | 15/33 | 14/27 |
| Tuned Q5_K_M | **281/346** | **81.2%** | 160/203 | 82/83 | 19/33 | 20/27 |
Failure classes — base: 101 false calls, 43 asked-instead, 43 wrong tool, 28 wrong args. Q4: 30 false calls, 25 wrong args, 12 wrong tool, 3 no-clarify, 2 missed tool. Q5: 15 false calls, 26 wrong args, 8 wrong tool, 8 missed tool, 3 no-clarify.
Decision latency (laptop CPU, other work running): median ~3.1–3.5 s for all three; Q4/Q5 are not slower than the base.
Held-out set (46, used earlier to tune prompts, so only semi-clean): tuned Q4 **44/46**, against 36/46 for the 4B base. Golden and edge were not run on the tuned model before the run was paused.

## Where it fails (Q4, by group)
no-tool/refuse 17/32 wrong · explicit calls 16/87 (mostly arguments) · speech-error calls 11/32 · missing-slot clarify 7/10 · no-state clarify 4/10 · multi-call 5/26. Typical: it calls the nearest tool for things no tool does ("turn off the lights" → `app_control`, "check my bank balance" → web search, "cancel the client call" → tasks delete), and an inherited place for "and in london" with no state.

## Decision / why we moved on
Tuning more than doubled accuracy and removed the base model's inability to chat. It is not ready to ship (refuse/no-tool and arguments are weak, one labeler on the test set). The first end-to-end Pi run still uses the untuned base (your decision); v3 data targets the failure groups above without copying test sentences.
