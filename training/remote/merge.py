"""Merge a LoRA adapter into the bf16 base and save a plain Hugging Face model (then convert with llama.cpp).

    python merge.py --adapter out/adapter-epoch2 --out merged
"""

import argparse

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

ap = argparse.ArgumentParser()
ap.add_argument("--adapter", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--model", default="Qwen/Qwen3-1.7B")
a = ap.parse_args()
base = AutoModelForCausalLM.from_pretrained(a.model, torch_dtype=torch.bfloat16)
merged = PeftModel.from_pretrained(base, a.adapter).merge_and_unload()
merged.save_pretrained(a.out, safe_serialization=True)
AutoTokenizer.from_pretrained(a.model).save_pretrained(a.out)
print("merged ->", a.out)
