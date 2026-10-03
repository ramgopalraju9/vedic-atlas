"""Measure prompt size per section (and optionally generation speed).

Run from the repo root:
    python scripts/measure_prompt_budget.py            # token counts only (fast, no weights)
    python scripts/measure_prompt_budget.py --speed    # also load the model and time a call

Counts use the real GGUF tokenizer (vocab_only load), so they are exact for
the configured model. Used to set and re-check the per-stage token budgets.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.config import load_full_config  # noqa: E402


def _tokenizer(model_path: Path):
    import llama_cpp

    return llama_cpp.Llama(model_path=str(model_path), vocab_only=True, verbose=False)


def count(llm, text: str) -> int:
    return len(llm.tokenize(text.encode("utf-8"), add_bos=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", action="store_true", help="load the model and time prefill + generation")
    args = ap.parse_args()

    cfg = load_full_config()
    model_path = Path(cfg.inference.model_path)
    if not model_path.is_absolute():
        model_path = ROOT / model_path
    llm = _tokenizer(model_path)

    from service.agent.persona import VEDA_SYSTEM_PROMPT

    sections = {
        "persona (VEDA_SYSTEM_PROMPT)": VEDA_SYSTEM_PROMPT,
    }
    try:
        from tpa.filestore.file_prompt_store import FilePromptStore
        store = FilePromptStore()
        for name in store.names():
            sections[f"prompt file: {name}"] = store.get(name)
    except Exception:
        pass

    print(f"model: {model_path.name}   n_ctx: {cfg.inference.n_ctx}\n")
    total = 0
    for name, text in sections.items():
        n = count(llm, text)
        total += n
        print(f"{n:>6} tok  {name}")
    print(f"{total:>6} tok  TOTAL static text listed above\n")

    if args.speed:
        full = llm.__class__(model_path=str(model_path), n_ctx=cfg.inference.n_ctx, verbose=False)
        prompt = VEDA_SYSTEM_PROMPT + "\n\nUSER: what are my tasks?"
        n_prompt = count(full, prompt)
        t0 = time.perf_counter()
        out = full.create_chat_completion(messages=[{"role": "user", "content": prompt}], max_tokens=40)
        dt = time.perf_counter() - t0
        n_out = out["usage"]["completion_tokens"]
        print(f"speed: prompt={n_prompt} tok, generated={n_out} tok in {dt:.1f}s  ({n_out / dt:.1f} tok/s incl. prefill)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
