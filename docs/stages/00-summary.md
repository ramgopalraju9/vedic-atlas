# Veda — stage-by-stage summary (Oct 7–9 2026)

One page for the whole path; each stage has its own file (`01-…` to `11-…`) with metrics, failures and the reason we moved on. Numbers come from saved runs in `tests/eval/results/`, `training/datasets/build_v2/report.json` and `docs/HANDOFF.md`. "Estimate" marks anything not measured.

## The path in one table
| # | Stage | Result | Where it fell short → why we moved on |
|---|---|---|---|
| 1 | Unified control decode (one grammar-constrained decode + pure dispatch policy) | 4B: golden 43/51 (84%), held-out 28/40 (70%); latency ~14.6 s median | Accuracy gate not cleared; far too slow → keep design, change model and training |
| 2 | Gmail / Calendar / clock tools (16 tools) | golden 46/57, held-out 36/46, 12/13 new cases | Prompt full (2,597/2,600 tokens); model invents times → code guards, prompt is a hard budget |
| 3 | Reminders (no LLM), `veda login`, startup | Unit-tested, no accuracy metric | Model-free parts are solid → focus returns to routing |
| 4 | Guards, edge cases, removed "hey veda" guard | 4B: edge 14/27; golden 58/70; held-out 36/46 | Guards stop damage, not wrong choices → need a better router |
| 5 | Memory/recall (summary + exchange RAG) | right summary 10/12 (was 9/12); false memory on ordinary questions 4/16 (was 15/16 base) | Small self-written test; stopped at per-exchange index by decision |
| 6 | Model size choice | 4B 58/70 · 1.7B 35/70 · 0.6B 19/70 (golden) | 1.7B is fast enough for the Pi but inaccurate → fine-tune the 1.7B router only |
| 7 | Training data | 4,313 samples (3,890 train / 423 val), 0 invalid; second labeler 88% exact, 99% same tool | refuse 5%, multi-call light, warnings unreviewed → feeds v3 |
| 8 | Training (A100, LoRA, 49 min) | val loss 2.09 → 0.075; Q4 1.1 GB, Q5 1.3 GB | Loss ≠ accuracy → evaluate the quantized files |
| 9 | Evaluation on an unseen 346-case set | **base 37.9% → tuned Q4 78.9%, Q5 81.2%**; held-out 44/46 | refuse/no-tool 15–19/33, arguments, speech errors → not shippable yet |
| 10 | Prompt patch + place guard | 4B after prompt edit: golden 55/70, held-out 31/46 (worse) | Prompt edit reverted, guard kept → stop patching, enrich data |
| 11 | Repo + setup scripts | 99 files committed/pushed; run scripts fixed | SQLAlchemy blocked on this laptop; docs not pushed |

## Accuracy trajectory (what each number means)
- **4B base on our sets:** 84% → 83% (golden), 70% → 78% (held-out): gains from tools/guards/prompt, but 5–7 s per decision.
- **1.7B base:** golden 50%, held-out 54%, edge 26%; on the clean set 37.9% with 0/83 chat and 0/33 refuse (it never just answers or declines).
- **1.7B tuned:** clean 78.9% (Q4) / 81.2% (Q5); held-out 44/46; decision median ~3.1 s on the laptop, no slower than the base.
- Latency numbers are CPU with other work running; **nothing has been measured on a Pi.**

## Decisions made (and by whom)
- Voice-first product: typing/quote edge cases do not matter (you).
- Keep the code guards, drop the "hey veda" guard (you). Stop memory work after the per-exchange index (you).
- Fine-tune **only the router** and **only the 1.7B**; the chat model stays untouched (you). Eight labeling decisions accepted (you).
- Generated, scrubbed training data; nothing personal or from `.env`/DB/eval sets leaves the machine (you + rule).
- Use fewer agents (you). First Pi end-to-end run uses the untuned base; the tuned router waits (you).
- MoE rejected for the Pi on memory grounds (reasoned, not measured).

## What is still open
1. **v3 training data** (not now): more refuse/no-tool, no-state clarification and "stale ACTIVE" twins, multi-call, speech-error variants, argument precision; review the 56 grounding warnings; optionally the seed paraphrases (g11). Build it from category-level failure counts, not test sentences, so the clean set stays clean; get a second labeler on the test set.
2. **Retrain constraint:** the tuned model only works with the prompt it was trained on; any change to `control_stage.md` or the tool text means rebuilding the data and retraining.
3. **Router and chat share one model path** in the current profile; a separate router model beside an untouched chat model needs a code change.
4. **Not measured:** the Pi (latency, memory, prefix-cache), tuned golden/edge runs, same-day recall after topic changes, Q5 vs Q4 beyond the clean set.
5. **Uncommitted/unpushed:** the place guard (`place_policy.py`, `dispatch_policy.py`, tests), commit `8f4a497`, and these stage files (`docs/` is git-ignored).
6. **Environment:** SQLAlchemy blocked by Windows Application Control on this laptop.
