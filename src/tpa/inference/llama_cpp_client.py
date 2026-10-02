"""LlamaCppClient – InferencePort implementation via llama-cpp-python.

* New, no donor equivalent. Grounded in ADR-001 (llama.cpp as the
advanced/Pi fallback backend) and ADR-008 (single quantized SLM, Q5_K_M
preferred). Loads a local GGUF file directly – no server process. Fails
loudly at construction if the model file is missing rather than
attempting any network fetch, per the "no runtime auto-download when
offline_mode is enforced" rule (REQ-M-02).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from domain.ports.inference_port import InferencePort, InferenceTimeoutError


class LlamaCppClient:
    """Implements InferencePort by loading a local GGUF model via llama-cpp-python."""

    def __init__(
        self,
        model_path: str | Path,
        n_ctx: int = 4096,
        n_threads: int | None = None,
        timeout: float = 120.0,
        num_predict: int = 512,
    ):
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(
                f"GGUF model not found at {path} – this build does not auto-download models. "
                "Run the installer's model-fetch step first."
            )
        import llama_cpp  # imported lazily so this module can be inspected without the dependency installed

        self._llama = llama_cpp.Llama(model_path=str(path), n_ctx=n_ctx, n_threads=n_threads, verbose=False)
        self._timeout = timeout
        self._num_predict = num_predict

    @property
    def name(self) -> str:
        return "llama_cpp"

    def _prompt_messages(self, prompt: str, system: str) -> list[dict]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def complete(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        timeout: int | None = None,
        *,
        num_predict: int | None = None,
    ) -> str:
        # llama-cpp-python's create_chat_completion is synchronous/blocking –
        # run it off the event loop so it doesn't stall other coroutines.
        # service/inference/single_flight.py is what guarantees only one call
        # runs at a time (it did not exist when this file was first written).
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._llama.create_chat_completion,
                    messages=self._prompt_messages(prompt, system),
                    max_tokens=num_predict if num_predict is not None else self._num_predict,
                ),
                timeout=timeout or self._timeout,
            )
        except asyncio.TimeoutError as exc:
            raise InferenceTimeoutError(f"llama.cpp request timed out: {exc}") from exc
        return result["choices"][0]["message"]["content"]

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
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        loop.call_soon_threadsafe(queue.put_nowait, delta)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        asyncio.get_event_loop().run_in_executor(None, _produce)

        while True:
            if cancel_event is not None and cancel_event.is_set():
                return
            item = await queue.get()
            if item is None:
                return
            yield item
