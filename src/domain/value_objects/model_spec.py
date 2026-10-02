"""ModelSpec — which local model, at what quantization and context length.

New — grounded in the fields introduced by ADR-008 (model class: quantized
SLM) and reserved for ADR-009 (specific model selection): `model_id` is the
Ollama/GGUF identifier, `quantization` is e.g. "Q5_K_M", `n_ctx` is the
context window (ADR-008 recommends 8192 on the single-model Pi profile),
`n_threads` is optional and defaults to the backend's own choice.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    """Which model to load and how, for a given inference profile."""

    model_id: str
    quantization: str
    n_ctx: int = 4096
    n_threads: int | None = None