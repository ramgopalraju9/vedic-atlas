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
