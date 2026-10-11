# Stage 6 — Which model, and why fine-tune the 1.7B

*Oct 9 2026 (00:07–00:25 runs, saved in `tests/eval/results/`).*

## Goal
Pick the model for the Raspberry Pi 5 (8 GB). The 4B is too slow; the question was whether a smaller one could be good enough, or fixed by training.

## Metrics — same prompt, same sets, temperature 0.1, laptop CPU
| Model | Golden (70) | Held-out (46) | Edge (27) | Decision latency |
|---|---|---|---|---|
| Qwen3-4B Q4 | 58 (83%) | 36 (78%) | 14 (52%) | ~5–7 s warm (14.6 s median earlier, before prefix cache) |
| Qwen3-1.7B Q4 | 35 (50%) | 25 (54%) | 7 (26%) | median ~3.0 s |
| Qwen3-0.6B Q4 | 19 (27%) | 15 (33%) | 4 (15%) | median ~3.4 s (edge run) |

## Where it failed
The smaller the model, the more it either called a tool for everything or asked a question instead of answering. Prompt edits moved failures around without raising the total.

## Decision / why we moved on
- **Fine-tune the 1.7B only, and only the router (control decode).** The chat model that writes replies stays the untouched base, so general knowledge and tone do not change. New tools can still be added through the prompt.
- A tuned model has to be served with the **exact prompt it was trained on** (static prompt, tool list, format). Changing `control_stage.md` later means retraining (this bit us in Stage 10).
- The 4B stays the fallback and the first end-to-end Pi run uses an untuned base model (your decision; the tuned router waits).
- Training must happen on rented GPU: this laptop (i5-13420H, RTX 2050 4 GB, CPU-only torch) runs the 1.7B at ~16 tokens/s, so local training is infeasible.
