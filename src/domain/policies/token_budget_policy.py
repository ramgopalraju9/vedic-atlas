"""TokenBudgetPolicy — per-stage prompt budgets for the quantized local model.

The model runs on CPU with a small context window, and prompt prefill is the
dominant latency cost (measured: ~2 tok/s end to end), so every prompt stage
has a hard token budget and a deterministic trimming order. Pure arithmetic:
the actual token counter is injected by the caller (exact tokenizer when the
backend offers one, `estimate_tokens` otherwise).
"""

from dataclasses import dataclass

# Conservative chars-per-token for English text on Qwen-family vocabularies.
# Measured with scripts/measure_prompt_budget.py; erring low over-counts tokens,
# which keeps prompts safely inside budget.
_CHARS_PER_TOKEN = 3.4


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (never 0 for non-empty text)."""
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN) + 1)


@dataclass(frozen=True)
class PromptBudgets:
    """Max prompt tokens per stage (system + prompt together)."""

    call: int = 700       # Stage A: pick the tool, fill arguments
    narrate: int = 500    # Stage B: phrase the tool result
    chat: int = 1500      # plain conversation, no tools
    route: int = 1900     # LLM routing: persona + cues + ~65 examples (fixed, cached after warm-up) + history + the message
    call_history_turns: int = 2
    chat_history_turns: int = 8
    observation_max: int = 320  # a tool result is cut to this many tokens before narration
