"""OnnxEmbeddingProvider — EmbeddingPort implementation: a local BGE-style ONNX model run with onnxruntime.

Memory & Recall (Epic 4) embeds facts and conversation summaries so a question like "what is my fav sweet?"
can find "User mentioned gulab jamun as their favourite sweet" by meaning, even when the words differ.

Why onnxruntime + tokenizers directly instead of the `fastembed` library (ADR-005 named fastembed):
fastembed pulls in extra compiled packages for features Veda does not use (sparse/BM25: mmh3, py-rust-stemmers,
loguru). One of them is blocked by Windows Application Control on the development machine, which made
`import fastembed` fail outright, and every extra native wheel is another thing that can fail on a Raspberry Pi.
Dense text embedding only needs a tokenizer and an ONNX session, both already dependencies (faster-whisper and
the wake-word / Piper models use them), so this adapter has no extra dependencies at all.

The model is staged, never downloaded at runtime (REQ-M-02): `data/bge-small-en-v1.5/` must contain
`model.onnx` and `tokenizer.json` (scripts/setup_pi.sh fetches them from BAAI/bge-small-en-v1.5).

BGE pooling: the embedding is the hidden state of the first ([CLS]) token, L2-normalised, which is also what
makes cosine similarity a plain dot product in the vector store.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from domain.value_objects.embedding import Embedding

_MODEL_FILES = ("model.onnx", "onnx/model.onnx", "model_optimized.onnx", "model_quantized.onnx")
_MAX_TOKENS = 512          # BGE-small's context limit; longer text is truncated, not rejected
_ORT_THREADS = 2           # a short sentence is cheap; leave the cores to the language model


class OnnxEmbeddingProvider:
    """Implements EmbeddingPort with a local ONNX sentence-embedding model."""

    def __init__(self, model_dir: str | Path, model_id: str = "BAAI/bge-small-en-v1.5"):
        directory = Path(model_dir)
        model_file = next((directory / name for name in _MODEL_FILES if (directory / name).is_file()), None)
        tokenizer_file = directory / "tokenizer.json"
        if model_file is None or not tokenizer_file.is_file():
            raise FileNotFoundError(
                f"embedding model not found in {directory} - it needs model.onnx and tokenizer.json "
                "(this build never downloads models at runtime). Run scripts/setup_pi.sh, or download "
                "onnx/model.onnx and tokenizer.json from https://huggingface.co/BAAI/bge-small-en-v1.5 "
                "into that folder."
            )
        import onnxruntime as ort  # imported lazily, see LlamaCppClient's rationale
        from tokenizers import Tokenizer

        self._model_id = model_id
        self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
        self._tokenizer.enable_truncation(max_length=_MAX_TOKENS)

        options = ort.SessionOptions()
        options.intra_op_num_threads = _ORT_THREADS
        options.log_severity_level = 3  # errors only
        self._session = ort.InferenceSession(str(model_file), sess_options=options, providers=["CPUExecutionProvider"])
        self._input_names = {i.name for i in self._session.get_inputs()}
        outputs = [o.name for o in self._session.get_outputs()]
        self._output = "last_hidden_state" if "last_hidden_state" in outputs else outputs[0]

    @property
    def model_id(self) -> str:
        return self._model_id

    def _encode(self, text: str) -> tuple[float, ...]:
        import numpy as np

        encoded = self._tokenizer.encode(text or "")
        feeds = {
            "input_ids": np.array([encoded.ids], dtype=np.int64),
            "attention_mask": np.array([encoded.attention_mask], dtype=np.int64),
        }
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(feeds["input_ids"])
        hidden = self._session.run([self._output], feeds)[0]      # (1, tokens, dim)
        vector = hidden[0, 0].astype(np.float64)                  # the [CLS] token
        norm = float(np.linalg.norm(vector))
        if norm > 0.0:
            vector = vector / norm
        return tuple(float(x) for x in vector)

    async def embed(self, text: str) -> Embedding:
        vector = await asyncio.to_thread(self._encode, text)
        return Embedding(vector=vector, model_id=self._model_id)
