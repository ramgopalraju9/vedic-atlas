"""LoRA fine-tune of the router on one GPU. Runs on the rented machine; needs only torch, transformers, peft.

    python train_lora.py --data . --out out --epochs 2            # full run
    python train_lora.py --data . --out smoke --limit 24 --epochs 1   # smoke test

Data: train.jsonl / val.jsonl with {"prompt", "completion"}. The prompt is tokenised on its own (as the server does) and the completion
(label JSON + <|im_end|>) is appended, so the loss covers only the answer and the token boundary matches serving.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup


def load(path: Path, tok, limit: int, max_len: int):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        p = tok(r["prompt"], add_special_tokens=False)["input_ids"]
        c = tok(r["completion"], add_special_tokens=False)["input_ids"]
        if len(p) + len(c) > max_len:
            continue
        rows.append((p + c, [-100] * len(p) + c))
        if limit and len(rows) >= limit:
            break
    return rows


def batches(rows, size, shuffle, seed):
    order = list(range(len(rows)))
    if shuffle:
        random.Random(seed).shuffle(order)
    for i in range(0, len(order), size):
        chunk = [rows[j] for j in order[i:i + size]]
        n = max(len(x[0]) for x in chunk)
        ids = torch.full((len(chunk), n), 151643, dtype=torch.long)
        lab = torch.full((len(chunk), n), -100, dtype=torch.long)
        att = torch.zeros((len(chunk), n), dtype=torch.long)
        for k, (a, b) in enumerate(chunk):
            ids[k, :len(a)] = torch.tensor(a)
            lab[k, :len(b)] = torch.tensor(b)
            att[k, :len(a)] = 1
        yield ids.cuda(), lab.cuda(), att.cuda()


@torch.no_grad()
def evaluate(model, rows, micro):
    model.eval()
    total, count = 0.0, 0
    for ids, lab, att in batches(rows, micro, False, 0):
        out = model(input_ids=ids, attention_mask=att, labels=lab)
        n = int((lab[:, 1:] != -100).sum())
        total += out.loss.item() * n
        count += n
    model.train()
    return total / max(count, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--model", default="Qwen/Qwen3-1.7B")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--micro", type=int, default=4)
    ap.add_argument("--accum", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=3200)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    torch.manual_seed(a.seed)
    a.out.mkdir(parents=True, exist_ok=True)

    tok = AutoTokenizer.from_pretrained(a.model)
    train = load(a.data / "train.jsonl", tok, a.limit, a.max_len)
    val = load(a.data / "val.jsonl", tok, a.limit, a.max_len)
    print(f"train {len(train)} val {len(val)} tokens/epoch {sum(len(x[0]) for x in train):,}", flush=True)

    model = AutoModelForCausalLM.from_pretrained(a.model, torch_dtype=torch.bfloat16, attn_implementation="sdpa").cuda()
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model = get_peft_model(model, LoraConfig(r=a.rank, lora_alpha=2 * a.rank, lora_dropout=0.05, task_type="CAUSAL_LM",
                                              target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    model.print_trainable_parameters()
    params = [p for p in model.parameters() if p.requires_grad]
    for p in params:
        p.data = p.data.float()            # adapter weights in fp32, base stays bf16
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.0)
    steps_per_epoch = math.ceil(len(train) / (a.micro * a.accum))
    sched = get_cosine_schedule_with_warmup(opt, max(1, int(0.05 * steps_per_epoch * a.epochs)), steps_per_epoch * a.epochs)

    print(f"base val loss {evaluate(model, val, a.micro):.4f}", flush=True)
    model.train()
    t0, seen, step = time.time(), 0, 0
    for epoch in range(a.epochs):
        buf = 0
        for ids, lab, att in batches(train, a.micro, True, a.seed + epoch):
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss = model(input_ids=ids, attention_mask=att, labels=lab).loss
            (loss / a.accum).backward()
            seen += int(att.sum())
            buf += 1
            if buf == a.accum:
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                opt.step(); sched.step(); opt.zero_grad(set_to_none=True)
                buf, step = 0, step + 1
                if step % 5 == 0:
                    print(f"epoch {epoch + 1} step {step}/{steps_per_epoch * a.epochs} loss {loss.item():.4f} lr {sched.get_last_lr()[0]:.2e} "
                          f"{seen / (time.time() - t0):,.0f} tok/s elapsed {(time.time() - t0) / 60:.1f} min", flush=True)
        if buf:                                   # leftover partial accumulation
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step(); sched.step(); opt.zero_grad(set_to_none=True); step += 1
        print(f"== epoch {epoch + 1} val loss {evaluate(model, val, a.micro):.4f}", flush=True)
        model.save_pretrained(a.out / f"adapter-epoch{epoch + 1}")
    print(f"done in {(time.time() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
