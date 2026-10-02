"""OllamaClient — InferencePort implementation talking to a local Ollama server.

★ New, no donor equivalent (the donor's brain/ clients all shelled out to
cloud CLIs). Grounded directly in docs/roadmap/adr/ADR-001-local-llm-backend.md
(Ollama as the default local backend, HTTP API on localhost) and the
InferencePort contract (Batch 3). Only `httpx` may be imported here —
this file, plus llama_cpp_client.py, are the ONLY places allowed to
produce a completion, per REQ-M-02/M-03.

Tuning (added after benchmarking Qwen3-8B-Q4_K_M and Qwen2.5-0.5B on a
12-core CPU-only laptop — both measured, not assumed):

  * `options` was previously never sent, so Ollama silently used its own
    defaults and generation ran unbounded. A 0.5B base model answered
    "Say hello" with 225 tokens of rambling because nothing capped it.
    `num_predict` is the single most effective latency control on CPU.
  * `think=False` disables reasoning-model thinking blocks. Measured on
    Qwen3-8B: 245 tokens/73s with thinking vs 12 tokens/4.5s without —
    16x for identical visible output. Ignored by models without it.
  * `num_thread` is left unset by default: Ollama's own core detection is
    usually right, and over-subscribing threads makes things slower.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import httpx

from domain.ports.inference_port import InferencePort, InferenceTimeoutError


class OllamaClient:
    """Implements InferencePort against a local Ollama server (default: localhost:11434)."""

    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        model: str = "qwen-wire",
        timeout: float = 120.0,
        keep_alive: str = "30m",
        *,
        num_ctx: int = 4096,
        num_predict: int = 512,
        num_thread: int | None = None,
        num_batch: int | None = None,
        temperature: float = 0.7,
        think: bool = False,
        stop: tuple[str, ...] = (),
    ):
        self._host = host.rstrip("/")
        self._default_model = model
        self._timeout = timeout
        self._keep_alive = keep_alive
        self._num_ctx = num_ctx
        self._num_predict = num_predict
        self._num_thread = num_thread
        self._num_batch = num_batch
        self._temperature = temperature
        self._think = think
        self._stop = list(stop)

    @property
    def name(self) -> str:
        return "ollama"

    def _messages(self, prompt: str, system: str) -> list[dict]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _options(self, num_predict: int | None = None) -> dict[str, Any]:
        options: dict[str, Any] = {
            "num_ctx": self._num_ctx,
            "num_predict": num_predict if num_predict is not None else self._num_predict,
            "temperature": self._temperature,
        }
        if self._num_thread is not None:
            options["num_thread"] = self._num_thread
        if self._num_batch is not None:
            options["num_batch"] = self._num_batch
        if self._stop:
            options["stop"] = self._stop
        return options

    def _payload(
        self, prompt: str, system: str, model: str | None, *, stream: bool, num_predict: int | None = None
    ) -> dict[str, Any]:
        return {
            "model": model or self._default_model,
            "messages": self._messages(prompt, system),
            "stream": stream,
            "keep_alive": self._keep_alive,
            "think": self._think,
            "options": self._options(num_predict),
        }

    async def complete(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        timeout: int | None = None,
        *,
        num_predict: int | None = None,
    ) -> str:
        payload = self._payload(prompt, system, model, stream=False, num_predict=num_predict)
        try:
            async with httpx.AsyncClient(timeout=timeout or self._timeout) as client:
                resp = await client.post(f"{self._host}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data.get("message", {}).get("content", "")
        except httpx.TimeoutException as exc:
            raise InferenceTimeoutError(f"ollama request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"ollama request failed: {exc}") from exc

    async def stream(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        cancel_event: asyncio.Event | None = None,
        *,
        num_predict: int | None = None,
    ) -> AsyncIterator[str]:
        payload = self._payload(prompt, system, model, stream=True, num_predict=num_predict)
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", f"{self._host}/api/chat", json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if cancel_event is not None and cancel_event.is_set():
                            return
                        if not line:
                            continue
                        chunk = json.loads(line)
                        if chunk.get("done"):
                            return
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content
        except httpx.TimeoutException as exc:
            raise InferenceTimeoutError(f"ollama stream timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"ollama stream failed: {exc}") from exc

    # -- Boot-time checks -------------------------------------------------

    async def list_models(self) -> list[str]:
        """Model names this Ollama server currently has imported."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self._host}/api/tags")
            resp.raise_for_status()
            return [m.get("name", "") for m in resp.json().get("models", [])]

    async def verify_ready(self) -> None:
        """Fail loudly at boot if the server is down or the model isn't imported.

        Without this, the first user turn dies with an opaque 404 from deep
        inside a chat call; here the message can say exactly what to run.
        """
        try:
            available = await self.list_models()
        except Exception as exc:
            raise RuntimeError(f"Ollama not reachable at {self._host} — is it running? ({exc})") from exc

        wanted = self._default_model
        # Ollama reports "name:tag"; config usually omits the implicit ":latest".
        if not any(m == wanted or m.split(":")[0] == wanted.split(":")[0] for m in available):
            raise RuntimeError(
                f"Ollama model {wanted!r} is not imported. Available: {available or '(none)'}. "
                f"Import it with: ollama create {wanted} -f <Modelfile>"
            )

    async def warmup(self) -> None:
        """One tiny call so the first real turn doesn't pay the model load."""
        await self.complete(prompt="hi", system="", num_predict=1)