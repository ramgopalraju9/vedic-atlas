"""The router's timeouts and locks: queue time is not slowness, an abandoned call cannot overlap the next one,
and warm-up gets the long timeout it asks for."""

import asyncio
import threading
import time

import pytest

from domain.ports.inference_port import InferenceTimeoutError
from service.agent.llm_router import LlmRouter
from service.inference.single_flight import SingleFlight
from service.prompting.prompt_composer import PromptComposer
from tpa.filestore.file_prompt_store import FilePromptStore
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore

AGENTS = [("responder", "chat"), ("tasks", "to-do list")]
ANSWER = '{"agent": "tasks"}'


class _Inner:
    """A model that takes `delay` seconds to run and enforces `timeout` on its own run time, like the real clients."""

    name = "fake"

    def __init__(self, delay=0.0, answer=ANSWER):
        self.delay, self.answer, self.calls = delay, answer, []

    async def complete(self, prompt, system="", model=None, timeout=None, **kwargs):
        self.calls.append({"timeout": timeout})
        if timeout is not None and self.delay > timeout:
            await asyncio.sleep(timeout)
            raise InferenceTimeoutError("model too slow")
        await asyncio.sleep(self.delay)
        return self.answer


def _router(client, **kwargs):
    return LlmRouter(client, PromptComposer(FilePromptStore(), YamlToolManifestStore().load_all()), **kwargs)


# ---- HIGH-1: time spent queued behind the main model is not "this device is slow" -------------------------

def test_waiting_for_the_main_model_does_not_count_against_the_router_timeout():
    lock = asyncio.Lock()
    router = _router(SingleFlight(_Inner(delay=0.0), lock=lock), timeout_sec=0.1, queue_wait_sec=3.0)

    async def scenario():
        async with lock:                                   # the main model is busy for longer than the router timeout
            task = asyncio.create_task(router.route("add dentist friday", AGENTS))
            await asyncio.sleep(0.4)
        return await task

    decision = asyncio.run(scenario())
    assert decision is not None and decision.agent == "tasks"
    assert router.paused_for_sec() == 0


def test_giving_up_on_a_long_queue_skips_that_message_but_does_not_pause_routing():
    lock = asyncio.Lock()
    inner = _Inner(delay=0.0)
    router = _router(SingleFlight(inner, lock=lock), timeout_sec=0.05, queue_wait_sec=0.1)

    async def scenario():
        async with lock:
            skipped = await router.route("add dentist friday", AGENTS)   # queued past the limit
        return skipped, await router.route("add dentist friday", AGENTS)  # the lock is free again

    skipped, after = asyncio.run(scenario())
    assert skipped is None and router.paused_for_sec() == 0
    assert after is not None and after.agent == "tasks"


def test_a_model_that_runs_too_slowly_still_pauses_routing():
    router = _router(_Inner(delay=0.5), timeout_sec=0.05)
    assert asyncio.run(router.route("add dentist friday", AGENTS)) is None
    assert router.paused_for_sec() > 60


def test_the_router_hands_its_timeout_to_the_client():
    inner = _Inner()
    asyncio.run(_router(inner, timeout_sec=7.0).route("add dentist friday", AGENTS))
    assert inner.calls == [{"timeout": 7.0}]


def test_an_empty_answer_after_the_timeout_counts_as_too_slow():
    """GracefulDegradation (the main LLM routing) swallows a timeout and returns an empty string."""

    class _Swallowing:
        async def complete(self, prompt, system="", model=None, timeout=None, **kwargs):
            await asyncio.sleep(timeout + 0.02)
            return ""

    router = _router(_Swallowing(), timeout_sec=0.05)
    assert asyncio.run(router.route("add dentist friday", AGENTS)) is None
    assert router.paused_for_sec() > 60


def test_an_empty_answer_that_came_back_fast_is_a_plain_failure_not_a_pause():
    router = _router(_Inner(answer=""), timeout_sec=5.0)
    assert asyncio.run(router.route("add dentist friday", AGENTS)) is None
    assert router.paused_for_sec() == 0


# ---- HIGH-3: warm-up gets the long timeout it asks for ---------------------------------------------------

def test_warmup_passes_a_long_timeout_into_the_client():
    inner = _Inner()
    asyncio.run(_router(inner, timeout_sec=8.0).warmup(AGENTS))
    assert inner.calls[0]["timeout"] >= 60


def test_a_slow_warmup_does_not_pause_routing():
    router = _router(_Inner(delay=0.3), timeout_sec=0.05, queue_wait_sec=0.05)
    asyncio.run(router.warmup(AGENTS))
    assert router.paused_for_sec() == 0


# ---- HIGH-2: an abandoned call keeps the thread lock, so the next call cannot start on top of it ----------

class _FakeLlama:
    """Stands in for llama_cpp.Llama: the first call blocks until released, like a long prefill."""

    instances: list = []

    def __init__(self, **kwargs):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False
        _FakeLlama.instances.append(self)

    def set_cache(self, cache):
        pass

    def create_chat_completion(self, **kwargs):
        self.entered.set()
        if self.block:
            self.release.wait(10)
        return {"choices": [{"message": {"content": ANSWER}}]}


def _llama_clients(monkeypatch, tmp_path, shared_lock):
    import llama_cpp

    from tpa.inference.llama_cpp_client import LlamaCppClient

    monkeypatch.setattr(llama_cpp, "Llama", _FakeLlama)
    _FakeLlama.instances = []
    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    a = LlamaCppClient(model, model_lock=shared_lock)
    b = LlamaCppClient(model, model_lock=shared_lock)
    _FakeLlama.instances[0].block = True
    return a, b, _FakeLlama.instances[0], _FakeLlama.instances[1]


async def _abandon_a_then_start_b(a, b, llama_a, llama_b):
    with pytest.raises(InferenceTimeoutError):
        await a.complete("x", timeout=0.05)        # times out; its worker thread is still inside llama_a
    assert llama_a.entered.is_set()
    task = asyncio.create_task(b.complete("y", timeout=5))
    await asyncio.sleep(0.3)
    b_started_while_a_ran = llama_b.entered.is_set()
    llama_a.release.set()
    await task
    return b_started_while_a_ran


def test_clients_sharing_a_thread_lock_do_not_overlap_after_a_timeout(monkeypatch, tmp_path):
    a, b, llama_a, llama_b = _llama_clients(monkeypatch, tmp_path, threading.Lock())
    assert asyncio.run(_abandon_a_then_start_b(a, b, llama_a, llama_b)) is False


def test_without_the_shared_lock_the_second_model_would_start_on_top_of_the_abandoned_call(monkeypatch, tmp_path):
    """The control: this is the bug the shared lock fixes."""
    a, b, llama_a, llama_b = _llama_clients(monkeypatch, tmp_path, None)
    assert asyncio.run(_abandon_a_then_start_b(a, b, llama_a, llama_b)) is True
