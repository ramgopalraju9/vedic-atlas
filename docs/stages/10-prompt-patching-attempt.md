# Stage 10 — Patching the prompt and adding a guard (partly reverted)

*Oct 9 2026.*

## Trigger
Two pasted conversations: (a) after "what is the weather?" the next questions "which is best season to go there?" and "can you compare both places?" both returned Hyderabad weather; (b) a Thailand chat with a garbled web-search answer, a one-line headline, an invented "directions" capability and a date without a month.

## Cause found (reproduced on the 4B base)
1. The router anchors on `ACTIVE: get_weather` and calls the weather tool for a travel question.
2. `get_weather` treats an empty place as the home city, so the wrong tool *looks* right ("In Hyderabad it's 33 degrees").
3. Nothing checks the reply against the question.

## What we changed
- **Kept:** `place_policy` + a `dispatch_policy` veto: a weather call whose place is only "there/that place", or empty while the user points at another place, asks "Which place do you mean?". Plain "is it raining" and "weather here" still use home; a real inherited place still runs. 9 new tests (80 pass across the dispatch/control/profile/prompt suites).
- **Reverted:** a rule + example + removed chat examples in `control_stage.md`. Trimming to fit 2,600 tokens cost examples, and the 4B scored worse (golden 55/70 vs 58/70; held-out 31/46 vs 36/46, mostly pointless clarifying questions). Baselines came from earlier code, so it was not a clean A/B, and a prompt change would also have mismatched the prompt the tuned model was trained on.

## Decision / why we moved on
We cannot fix every behaviour with another rule; each prompt edit trades one failure for another. The next lever is **training data** for the 1.7B (v3), not more patching. A mixture-of-experts model was considered and rejected for the Pi: all experts must sit in RAM (a 30B-A3B needs ~17 GB) and the 2.2k-token prefill touches most experts anyway; smaller MoEs are untested. The 4B dense model remains a possible fallback fine-tune.
