# Stage 4 — Guards, edge cases and cleanup

*Oct 8–9 2026.*

## Goal
Find where the assistant breaks on real phrasing, decide what the model must handle and what the code must veto, and remove checks that did more harm than good.

## What we did
- Edge-case probe set `tests/eval/orchestrator_edge.yaml` (27 cases: calendar write/read, mail, "no tool for it", mentions/negations). Marked as a probe, never tuned against.
- Log analyses: why "hey veda!" listed mail; why "what's 1+1" answered blank; why a typo'd "what are we discusing" asked a question.
- **Guards**: you asked which checks were keyword-based. We kept the fail-closed vetoes (destructive target, calendar time, gmail_send after a draft, pronoun/ordinal targets for every call with a target parameter) and **removed the "hey veda" greeting guard**.
- Edge-case test file `test_edge_cases.py`, `test_guards.py`; `/doctor` cleanup.

## Metrics
| Run (4B, A1) | Result |
|---|---|
| Edge set | 14/27 (52%) |
| Golden / held-out at the end of this day | 58/70 (83%) / 36/46 (78%) |

## Where it failed
"No tool for it" cases (cancel/move/delete a calendar event, other-city time) and mentions ("my calendar app crashes") produced false calls; mail follow-ups ("the first one") were ambiguous.

## Decision / why we moved on
Code guards fix *dangerous* mistakes only. They cannot make the model choose well, and each prompt edit moved failures around instead of removing them. The same conclusion was reached again in Stage 10. From here the plan became: smaller model + training.
