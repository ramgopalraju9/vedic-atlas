# Stage 8 — Training on a rented A100

*Oct 9 2026. Code: `training/remote/train_lora.py`, `training/remote/merge.py`.*

## Goal
LoRA-tune Qwen3-1.7B on `build_v2`, produce quantized GGUFs the Pi can run.

## Setup
1× A100 SXM4 80 GB on vast.ai ($1.064/hr), 32 GB disk. Only `train.jsonl`, `val.jsonl` and two scripts were uploaded (no `.env`, database, seeds, eval or test sets). The base model came from Hugging Face on the box.

## Recipe
bf16 base + LoRA rank 16 (alpha 32, dropout 0.05) on all attention and MLP projections (17.4M trainable, 1.0%), fp32 adapter weights, lr 1e-4 with 5% warm-up and cosine decay, 2 epochs, effective batch 32 (4 × 8), gradient checkpointing, **loss on the answer only** (JSON + `<|im_end|>`), prompt tokenised separately exactly as the server does.
Then: merge into the bf16 base → `convert_hf_to_gguf` (f16) → `llama-quantize` Q4_K_M and Q5_K_M.

## Metrics
| Item | Value |
|---|---|
| Smoke test (32 samples, 4 steps) | val loss 1.76 → 1.32 |
| Base val loss at start of the full run | 2.09 |
| Val loss after epoch 1 / epoch 2 | 0.0816 / **0.0752** |
| Throughput | ~6,100 tokens/s → 49.0 min for 244 steps |
| Cost | ≈ $0.90 for training; whole rental ≈ 2–3 h ≈ $2–3 (estimate) |
| Files | Q4_K_M 1.1 GB · Q5_K_M 1.3 GB · f16 3.4 GB (+ both adapters, 134 MB) in `data/router/` |
| Chat template | text differs from the original 1.7B but renders identically for our message shape |

## Where it failed / surprises
`nvidia-smi` shows "No running processes" inside the container (a display limit, GPU was at 100% and 62 GB). The disk was tight (32 GB), so intermediate files were removed as they were consumed. Loss alone says only that the format was learned.

## Decision / why we moved on
Evaluate the quantized files, not the training loss (Stage 9). The instance is yours to stop; nothing further is needed from it.
