# Stage 7 — Training data (synthetic, scrubbed, audited)

*Oct 9 2026. Code: `training/` (`sample.py`, `render.py`, `validate.py`, `build.py`, `check.py`, `augment.py`, `seeds.py`, `review.py`, `make_testset.py`). Rules: `training/labeling_guide.md`, `training/GENERATOR_BRIEF.md`.*

## Goal
~4,000 labelled examples that teach the router exactly what the serving prompt asks for, with no personal data and no copies of the test sets.

## What we built
- **Labeling guide**: eight borderline decisions accepted by you (stable trivia → chat; umbrella/jacket → forecast; live things with no tool → refuse; other-city time → refuse; cancel/move/delete calendar event → refuse; joke → chat; "and in Delhi" with no state → clarify; bare "what's happening" → clarify).
- **Renderer** produces the exact serving text (system + volatile ACTIVE/RECENT/TODAY/USER + `/no_think`); a test checks it against the GGUF chat template.
- **Validator** (types, enums, ranges, flags, clarification rules, leak phrases, grounding) then `dispatch_policy.resolve` must agree with the expected route. Invalid labels are dropped, not repaired.
- **Generators**: Opus agents per topic; usage limit (HTTP 429) after launching 11 at once, so only the three missing ones were rerun (you asked for fewer agents).
- **Build**: dedupe → drop copies of eval sets (exact + cosine ≥ 0.97) → cap forecast samples → tool-subset variants (15%) → split by paraphrase group (val 10%).

## Metrics (`training/datasets/build_v2`)
| Item | Value |
|---|---|
| Generated before filtering | 4,084 (g01 588 · g02 372 · g03 205 · g04 370 · g05 356 · g06 334 · g07 527 · g08 381 · g09 414 · g10 313 · g12 224) |
| Dropped | 53 duplicates · 96 eval copies · 121 forecast over the cap (230) |
| Added | 499 tool-subset variants |
| Kept | **4,313** → train 3,890 · val 423 · invalid 0 |
| Decision mix | call 2,525 · chat 1,277 · clarify 298 · refuse 213 |
| Tokens | prompt mean 2,207 · completion mean 22 · ≈9.6M per epoch |
| Privacy scan | no private names; only `example.com` addresses; seeds in `training/data/` (git-ignored) never uploaded |

## Quality audit
A different Opus agent re-labelled a stratified 400 samples **without seeing the labels**.
- Exact agreement 352/400 (**88%**); same kind of decision 392/400; same tools 393/400. About 40 of the 48 differences were wording only.
- Real errors found and fixed: one wrong city ("weather in new york" labelled London); a wrongly clarified two-intent request; a reversed call order; one "the one before" that would re-read the same mail; two trivia follow-ups that should be chat.
- Open ambiguities (left as is): "book a cab" (refuse vs chat); "make it quieter" at volume 30 (ask vs lower); "the top one" / "the second one" mail; "rest of the week" start day.

## Where it is weak
Refuse is ~5% of the set (target 7%), multi-call 123 (target ~160), only ~9% of examples have no state/pronoun twins. The 56 grounding warnings were not reviewed. A separate set of seed paraphrases (g11) was never generated.

## Decision / why we moved on
Good enough for a first training run; the weak classes are exactly where Stage 9 shows failures, so they define the v3 data (see the summary).
