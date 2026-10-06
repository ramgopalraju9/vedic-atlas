"""LlmRouter — picks the specialist for a message the deterministic rules did not recognise.

Order of routing in the supervisor:
  1. manifest trigger patterns + keyword overlap (instant, free, exact on the golden set);
  2. THIS: one tiny constrained model call, only when step 1 matched nothing;
  3. the default (chat) agent if the model call fails or answers nonsense.

The call is cheap and safe by construction: a ~300-token prompt, a JSON schema whose
`agent` is an enum of the real agent names (the model cannot invent one), temperature 0
and a handful of output tokens. Every decision is logged as `[route] llm -> <agent>`.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Callable, Sequence

from core.logging_config import logger
from domain.entities.conversation import Turn
from domain.entities.route_decision import RouteDecision
from domain.policies.tool_call_schema import build_route_schema
from domain.ports.inference_port import InferencePort, InferenceTimeoutError
from service.prompting.prompt_composer import PromptComposer


_FOLLOW_UP_MAX_WORDS = 6  # recent turns are only included for messages this short
_SLOW_DEVICE_PAUSE_SEC = 600.0  # after a service-time timeout, route by rules only for this long (see LlmRouter.route)
_WARMUP_MIN_SERVICE_SEC = 60.0  # the first prefill of the long router prompt is slow on a small device


class LlmRouter:
    def __init__(
        self,
        client: InferencePort,
        composer: PromptComposer,
        *,
        model: str | None = None,
        num_predict: int = 24,
        timeout_sec: float = 30.0,
        queue_wait_sec: float = 30.0,
        history: Callable[[], Sequence[Turn]] | None = None,
    ):
        self._client = client
        self._composer = composer
        self._model = model
        self._num_predict = num_predict
        self._timeout = timeout_sec          # SERVICE time: how long the model may take once it is actually running
        self._queue_wait = queue_wait_sec    # how long the call may wait behind the main model before it is given up on
        self._history = history
        self._paused_until = 0.0

    def paused_for_sec(self) -> float:
        """Seconds of the slow-device pause still to run (0 = the router is active)."""
        return max(0.0, self._paused_until - time.monotonic())

    @staticmethod
    def _responder_last(agents: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
        """Chat goes last in the prompt and in the answer enum: a small model leans toward the first option it
        sees, and "responder" first made it send weather and news questions to plain chat."""
        specialists = [a for a in agents if a[0] != "responder"]
        return specialists + [a for a in agents if a[0] == "responder"]

    async def warmup(self, agents: Sequence[tuple[str, str]]) -> None:
        """Evaluate the long fixed router prompt once at boot, so the first real message is not the slow one.

        Never raises and never trips the slow-device pause: a failed warm-up only means the first message is slower.
        """
        agents = self._responder_last(agents)
        names = [name for name, _ in agents]
        if len(names) < 2:
            return
        prompt = self._composer.route_stage(agents, "hello there", ())
        started = time.perf_counter()
        service_sec = max(self._timeout, _WARMUP_MIN_SERVICE_SEC)
        try:
            await asyncio.wait_for(
                self._client.complete(
                    prompt=prompt.prompt, system=prompt.system, model=self._model, timeout=service_sec,
                    json_schema=build_route_schema(names), temperature=0.0, num_predict=self._num_predict,
                ),
                timeout=service_sec + self._queue_wait,
            )
            logger.info(f"[route] router warmed up in {int((time.perf_counter() - started) * 1000)} ms")
        except Exception as e:
            logger.warning(f"[route] router warm-up skipped ({type(e).__name__}: {e})")

    async def route(self, message: str, agents: Sequence[tuple[str, str]]) -> RouteDecision | None:
        """`agents` = (name, description) of every routable agent. None on any failure.

        A timeout means this device cannot afford a routing call (a Raspberry Pi can take 30 s+ for the prompt
        alone). Rather than make every unmatched message wait that long again, routing falls back to rules only
        for `_SLOW_DEVICE_PAUSE_SEC`, then tries once more.
        """
        agents = self._responder_last(agents)
        names = [name for name, _ in agents]
        if len(names) < 2:
            return None
        if time.monotonic() < self._paused_until:
            return None
        short_follow_up = len(message.split()) <= _FOLLOW_UP_MAX_WORDS
        history = self._history() if (self._history and short_follow_up) else ()
        prompt = self._composer.route_stage(agents, message, history)
        started = time.perf_counter()
        try:
            # The client enforces `timeout` on the time the model actually runs; the outer limit only bounds how long
            # the call may sit queued behind the main model (which can be streaming a long reply).
            raw = await asyncio.wait_for(
                self._client.complete(
                    prompt=prompt.prompt, system=prompt.system, model=self._model, timeout=self._timeout,
                    json_schema=build_route_schema(names), temperature=0.0, num_predict=self._num_predict,
                ),
                timeout=self._timeout + self._queue_wait,
            )
            if not raw and time.perf_counter() - started >= self._timeout:
                # GracefulDegradation (used when the main LLM routes) swallows a timeout and returns "".
                raise InferenceTimeoutError("empty answer after the timeout")
            agent = json.loads(raw)["agent"]
        except InferenceTimeoutError:
            # The model ran and was too slow: this device cannot afford a routing call.
            self._paused_until = time.monotonic() + _SLOW_DEVICE_PAUSE_SEC
            logger.warning(
                f"[route] llm routing took longer than {self._timeout:.0f}s to run - too slow on this device; "
                f"routing by rules only for the next {int(_SLOW_DEVICE_PAUSE_SEC // 60)} min"
            )
            return None
        except asyncio.TimeoutError:
            # Never got its turn (or the backend ignored its timeout): not evidence that the device is slow, so
            # this message falls back to the rules but routing is NOT paused.
            logger.warning(
                f"[route] llm routing gave no answer within {self._timeout + self._queue_wait:.0f}s "
                "(waiting behind the main model?); this message is routed by the fallback"
            )
            return None
        except Exception as e:
            logger.warning(f"[route] llm routing failed ({type(e).__name__}: {e}); using the default agent")
            return None
        if agent not in names:  # cannot happen with a constrained schema; guard anyway
            logger.warning(f"[route] llm answered an unknown agent {agent!r}; using the default agent")
            return None
        ms = int((time.perf_counter() - started) * 1000)
        logger.info(f"[route] llm -> {agent} ({ms} ms, {prompt.tokens} prompt tokens) for {message[:70]!r}")
        return RouteDecision(agent=agent, ms=ms, prompt_tokens=prompt.tokens)
