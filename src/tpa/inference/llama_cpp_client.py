"""LlamaCppClient — InferencePort implementation via llama-cpp-python.

★ New, no donor equivalent. Grounded in ADR-001 (llama.cpp as the
advanced/Pi fallback backend) and ADR-008 (single quantized SLM, Q5_K_M
preferred). Loads a local GGUF file directly — no server process. Fails
loudly at construction if the model file is missing rather than
attempting any network fetch, per the "no runtime auto-download when
offline_mode is enforced" rule (REQ-M-02).

Thinking-mode suppression (`think=False`, the default): unlike OllamaClient,
llama-cpp-python's `create_chat_completion` has no `think`/`enable_thinking`
parameter of its own — Qwen3's chat template reacts to a `/no_think` marker
placed in the conversation text instead (the model's own documented
fallback for engines that don't support template-level thinking control).
That alone isn't fully reliable (a truncated generation can still leave a
dangling `<think>` block), so every return path also strips any
`<think>...</think>` block as a safety net - belt and suspenders, not
either/or.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import AsyncIterator

from domain.ports.inference_port import InferencePort, InferenceTimeoutError

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_UNCLOSED_THINK_RE = re.compile(r"<think>.*", re.DOTALL)


class LlamaCppClient:
    """Implements InferencePort by loading a local GGUF model via llama-cpp-python."""

    def __init__(
        self,
        model_path: str | Path,
        n_ctx: int = 4096,
        n_threads: int | None = None,
        timeout: float = 120.0,
        num_predict: int = 512,
        think: bool = False,
        prompt_cache_mb: int = 0,
    ):
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(
                f"GGUF model not found at {path} — this build does not auto-download models. "
                "Run the installer's model-fetch step first."
            )
        import llama_cpp  # imported lazily so this module can be inspected without the dependency installed

        self._llama = llama_cpp.Llama(model_path=str(path), n_ctx=n_ctx, n_threads=n_threads, verbose=False)
        if prompt_cache_mb > 0:
            # The context only reuses its KV cache when consecutive prompts share a start. Veda alternates between
            # chat, routing and tool-call prompts, each with a long fixed prefix, so every call re-read its whole
            # prompt. A RAM cache of evaluated prefixes lets each prompt type reuse its own prefix.
            self._llama.set_cache(llama_cpp.LlamaRAMCache(capacity_bytes=prompt_cache_mb * 1024 * 1024))
        self._timeout = timeout
        self._num_predict = num_predict
        self._think = think

    @property
    def name(self) -> str:
        return "llama_cpp"

    def _prompt_messages(self, prompt: str, system: str) -> list[dict]:
        if not self._think:
            # Qwen3's documented soft-switch for engines (like raw
            # llama-cpp-python) that don't expose chat-template-level
            # thinking control.
            prompt = f"{prompt} /no_think"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Safety net: remove any <think>...</think> block, or an unclosed
        one if generation was cut off mid-thought, that slipped through
        despite the /no_think prompt directive."""
        text = _THINK_BLOCK_RE.sub("", text)
        text = _UNCLOSED_THINK_RE.sub("", text)
        return text.strip()

    async def complete(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        timeout: int | None = None,
        *,
        num_predict: int | None = None,
        json_schema: dict | None = None,
        temperature: float | None = None,
    ) -> str:
        # llama-cpp-python's create_chat_completion is synchronous/blocking —
        # run it off the event loop so it doesn't stall other coroutines.
        # service/inference/single_flight.py is what guarantees only one call
        # runs at a time (it did not exist when this file was first written).
        kwargs: dict = {
            "messages": self._prompt_messages(prompt, system),
            "max_tokens": num_predict if num_predict is not None else self._num_predict,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if json_schema is not None:
            # Grammar-constrained decoding: the model cannot emit anything that
            # isn't valid JSON for this schema.
            kwargs["response_format"] = {"type": "json_object", "schema": json_schema}
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._llama.create_chat_completion, **kwargs),
                timeout=timeout or self._timeout,
            )
        except asyncio.TimeoutError as exc:
            raise InferenceTimeoutError(f"llama.cpp request timed out: {exc}") from exc
        content = result["choices"][0]["message"]["content"]
        return self._strip_thinking(content) if not self._think else content

    def count_tokens(self, text: str) -> int:
        """Exact token count with this model's own tokenizer (used for prompt budgets)."""
        return len(self._llama.tokenize(text.encode("utf-8"), add_bos=False))

    async def stream(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        cancel_event: asyncio.Event | None = None,
        *,
        num_predict: int | None = None,
    ) -> AsyncIterator[str]:
        # llama-cpp-python's streaming generator is synchronous; drain it on a
        # worker thread and forward chunks through an asyncio.Queue.
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()
        max_tokens = num_predict if num_predict is not None else self._num_predict

        def _produce():
            try:
                for chunk in self._llama.create_chat_completion(
                    messages=self._prompt_messages(prompt, system), stream=True, max_tokens=max_tokens
                ):
                    if cancel_event is not None and cancel_event.is_set():
                        break  # cancelled (e.g. the user muted): stop burning CPU on a reply nobody will hear
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        loop.call_soon_threadsafe(queue.put_nowait, delta)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        asyncio.get_event_loop().run_in_executor(None, _produce)

        if self._think:
            # Nothing to filter - pass chunks through as they arrive.
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    return
                item = await queue.get()
                if item is None:
                    return
                yield item
            return

        # Safety-net filtering: suppress any text between <think> and
        # </think>, tolerating the tag being split across chunk boundaries
        # by holding back a short tail until we're sure it isn't half a tag.
        in_think = False
        pending = ""
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return
            item = await queue.get()
            if item is None:
                if pending and not in_think:
                    yield pending
                return
            pending += item
            while True:
                if in_think:
                    idx = pending.find(_THINK_CLOSE)
                    if idx == -1:
                        pending = ""  # still inside the think block - discard
                        break
                    pending = pending[idx + len(_THINK_CLOSE):]
                    in_think = False
                    continue
                idx = pending.find(_THINK_OPEN)
                if idx == -1:
                    # Hold back a short tail in case "<think>" is split
                    # across this chunk and the next one.
                    safe_len = max(0, len(pending) - len(_THINK_OPEN))
                    if safe_len > 0:
                        yield pending[:safe_len]
                        pending = pending[safe_len:]
                    break
                if idx > 0:
                    yield pending[:idx]
                pending = pending[idx + len(_THINK_OPEN):]
                in_think = True
