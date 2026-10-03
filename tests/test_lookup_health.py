"""LookupHealthService: ok / failed / timed-out / not-configured probes."""

import asyncio

from service.lookup.health_service import HealthProbe, LookupHealthService


def _run(probes, timeout=1.0):
    return asyncio.run(LookupHealthService(probes, timeout_sec=timeout).run())


def test_ok_and_failure_are_reported_separately():
    async def good():
        return "sample answer"

    async def bad():
        raise RuntimeError("503 from upstream")

    out = _run([HealthProbe("weather", good), HealthProbe("fx", bad)])
    by = {r.name: r for r in out}
    assert by["weather"].ok and by["weather"].detail == "sample answer"
    assert not by["fx"].ok and by["fx"].configured and "503" in by["fx"].detail


def test_unconfigured_probe_makes_no_call():
    called = []

    async def check():
        called.append(1)
        return "x"

    out = _run([HealthProbe("web_search", check, configured=lambda: False, missing_hint="set TAVILY_API_KEY")])
    assert called == []
    assert not out[0].ok and not out[0].configured and "TAVILY_API_KEY" in out[0].detail


def test_slow_probe_times_out():
    async def slow():
        await asyncio.sleep(5)
        return "late"

    out = _run([HealthProbe("slow", slow)], timeout=0.05)
    assert not out[0].ok and "timed out" in out[0].detail
