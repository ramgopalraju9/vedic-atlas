"""Two models on one CPU take turns when their SingleFlight wrappers share a lock."""

import asyncio

from service.inference.single_flight import SingleFlight


class _Slow:
    name = "slow"

    def __init__(self, log, label):
        self._log, self._label = log, label

    async def complete(self, prompt, system="", model=None, timeout=None, **kwargs):
        self._log.append(f"{self._label} start")
        await asyncio.sleep(0.05)
        self._log.append(f"{self._label} end")
        return self._label


async def _run(a, b):
    return await asyncio.gather(a.complete("x"), b.complete("y"))


def test_wrappers_sharing_a_lock_run_one_at_a_time():
    log = []
    lock = asyncio.Lock()
    a, b = SingleFlight(_Slow(log, "main"), lock=lock), SingleFlight(_Slow(log, "router"), lock=lock)
    asyncio.run(_run(a, b))
    assert log in (["main start", "main end", "router start", "router end"],
                   ["router start", "router end", "main start", "main end"])


def test_wrappers_with_their_own_locks_overlap():
    log = []
    a, b = SingleFlight(_Slow(log, "main")), SingleFlight(_Slow(log, "router"))
    asyncio.run(_run(a, b))
    assert log[:2] == ["main start", "router start"]
