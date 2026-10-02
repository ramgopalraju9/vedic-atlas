"""SensorPort — lifecycle contract for anything that publishes AmbientEvents.

Donor: veda/sensors/base.py's BaseSensor, read in full. Only the lifecycle
contract (start/stop/is_running) is a Port — it has no I/O of its own.
The donor's concrete asyncio scaffolding (the polling loop, the
asyncio.Task management, the stop Event) is orchestration, not an
interface; it is staged as a shared base class at
service/sensing/sensor_registry.py in a later batch, not here.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class SensorPort(Protocol):
    """Start/stop lifecycle for a background event source."""

    def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    @property
    def is_running(self) -> bool:
        ...