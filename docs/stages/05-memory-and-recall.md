# Stage 5 — Memory and recall (RAG over summaries and exchanges)

*Oct 8–9 2026. Sources: `docs/HANDOFF.md`, `config/embedding.yaml`.*

## Goal
Make recall of earlier conversations reliable ("what did we talk about", "what did I say about Barcelona"), without injecting memory into ordinary questions ("what is 2 plus 2"), and without costing prompt tokens on the Pi.

## What we built
- bge-small ONNX embeddings, SQLite vector store, two searched sources: session **summaries** and each **question-and-answer exchange** (indexed right after the reply, no model call; `exchange_note_policy`).
- Floors by measurement: summary 0.62, exchange 0.70, margin 0.05, top-k 4, 280 chars per hit, 800 per block.
- **Conversation mode**: a question that nearly equals "what have we been talking about" (cosine ≥ 0.82, soft 0.76 when the router would ask or refuse) is answered from the newest earlier conversations by date, not by similarity.
- Per-source search (stops one source crowding out the other), summary backfill script, "today/tomorrow" labels on recalled items, compact profiles carry 0 summaries in the prompt but recall still searches them.

## Metrics
| Measurement | Before | After |
|---|---|---|
| Right summary in the block (12 self-written questions, 19 summaries) | 9/12 | 10/12 |
| Ordinary questions wrongly given memory (16) | 15/16 base · 9/16 old profile | 4/16 |
| Exchange notes at the summary floor (0.62): ordinary questions pulling in an old exchange | 10/16 | fixed by the 0.70 exchange floor |
| Specific-detail questions | — | score ≥ 0.70; ordinary ones ≤ 0.70; genuine repeats 0.8+ |
| Conversation-intent paraphrases | — | 0.82–1.0; specific questions ≤ 0.82; ordinary ≤ 0.77 |

## Where it failed
- A typo'd "discusing" scored 0.795 (below 0.82) → soft threshold 0.76 added.
- Summaries only exist after a 30-minute gap, hence the per-exchange index.
- Memory set is small (12 questions, self-written); no formal ~40-question test set was written.
- End-to-end same-day recall after a topic change has not been run (SQLAlchemy is blocked on this laptop, see Stage 11).

## Decision / why we moved on
Stopped memory work at the per-exchange index (your decision). Remaining risk is measurement, not design.
