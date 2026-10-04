"""A slow device (Raspberry Pi): a timed-out model call must not crash the server or slow every later message.

Regression: on the Pi the LLM router's call exceeded its 30 s timeout. The timeout only abandons the `await`:
the worker thread kept decoding, SingleFlight's lock had already been released, and the chat reply started a
second decode on the same llama.cpp context. llama.cpp is not thread-safe, so the server died and the CLI
printed "peer closed connection without sending complete message body (incomplete chunked read)".

No model needed: llama_cpp is replaced by a fake that detects overlapping calls.
Run: pytest tests/test_slow_device.py
"""

import asyncio
import sys
import threading
import time
import types

import pytest

from domain.ports.inference_port import InferenceTimeoutError
from service.agent.llm_router import LlmRouter
from service.prompting.prompt_composer import PromptComposer
from tpa.filestore.file_prompt_store import FilePromptStore
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore


class _FakeLlama:
    """Records how many calls are decoding at once. `slow` is the time one decode takes."""

    def __init__(self, **kwargs):
        self.slow = 0.3
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()

    def _enter(self):
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def _leave(self):
        with self._guard:
            self.active -= 1

    def create_chat_completion(self, messages, max_tokens=None, stream=False, **kwargs):
        if stream:
            return self._stream()
        self._enter()
        try:
            time.sleep(self.slow)
        finally:
            self._leave()
        return {"choices": [{"message": {"content": '{"agent": "responder"}'}}]}

    def _stream(self):
        self._enter()
        try:
            for word in ("hello", " there"):
                time.sleep(0.05)
                yield {"choices": [{"delta": {"content": word}}]}
        finally:
            self._leave()

    def set_cache(self, cache):
        pass


@pytest.fixture
def client(monkeypatch, tmp_path):
    fake = types.ModuleType("llama_cpp")
    fake.Llama = _FakeLlama
    fake.LlamaRAMCache = lambda capacity_bytes: None
    monkeypatch.setitem(sys.modules, "llama_cpp", fake)
    from tpa.inference.llama_cpp_client import LlamaCppClient

    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    return LlamaCppClient(model, think=True)      # think=True: pass text through untouched


def test_a_reply_never_decodes_while_a_timed_out_call_is_still_running(client):
    async def scenario():
        with pytest.raises(InferenceTimeoutError):
            await client.complete("route me", timeout=0.05)          # abandoned after 0.05 s, still decoding for 0.3 s
        started = time.monotonic()
        text = "".join([chunk async for chunk in client.stream("hi")])
        return text, time.monotonic() - started

    text, waited = asyncio.run(scenario())
    assert text == "hello there"
    assert client._llama.max_active == 1                             # two decodes never overlapped
    assert waited >= 0.2                                             # the reply waited for the abandoned call to finish


def test_two_timed_out_calls_in_a_row_still_never_overlap(client):
    async def scenario():
        for _ in range(2):
            with pytest.raises(InferenceTimeoutError):
                await client.complete("x", timeout=0.05)
        return await client.complete("y", timeout=5)

    asyncio.run(scenario())
    assert client._llama.max_active == 1


# ---- the router pauses itself on a slow device -------------------------------------------------

class _SlowClient:
    def __init__(self, delay):
        self.delay, self.calls = delay, 0

    async def complete(self, prompt, system="", model=None, timeout=None, **kwargs):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return '{"agent": "tasks"}'


def _router(client, timeout_sec):
    manifests = YamlToolManifestStore().load_all()
    return LlmRouter(client, PromptComposer(FilePromptStore(), manifests), timeout_sec=timeout_sec)


AGENTS = [("responder", "chat"), ("tasks", "to-do list")]


def test_after_a_timeout_routing_is_rules_only_so_later_messages_do_not_wait():
    client = _SlowClient(delay=0.5)
    router = _router(client, timeout_sec=0.05)

    async def scenario():
        first = await router.route("add dentist friday", AGENTS)
        second = await router.route("add dentist friday", AGENTS)
        return first, second

    first, second = asyncio.run(scenario())
    assert first is None and second is None
    assert client.calls == 1                       # the second message did not make a model call at all


def test_a_fast_device_keeps_routing_with_the_model():
    client = _SlowClient(delay=0.0)
    router = _router(client, timeout_sec=5)

    async def scenario():
        return await router.route("add dentist friday", AGENTS), await router.route("add dentist friday", AGENTS)

    first, second = asyncio.run(scenario())
    assert first.agent == "tasks" and second.agent == "tasks" and client.calls == 2


def test_the_router_tries_again_after_the_pause(monkeypatch):
    import service.agent.llm_router as module

    client = _SlowClient(delay=0.5)
    router = _router(client, timeout_sec=0.05)

    async def scenario():
        await router.route("add dentist friday", AGENTS)                  # times out, pauses
        router._paused_until = time.monotonic() - 1                       # pause over
        client.delay = 0.0
        return await router.route("add dentist friday", AGENTS)

    assert asyncio.run(scenario()).agent == "tasks" and client.calls == 2
