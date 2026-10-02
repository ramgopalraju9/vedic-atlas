"""SensorRegistry — shared start/stop asyncio lifecycle for background sensors.

Donor: veda/sensors/base.py's BaseSensor, read in full. Only the concrete
asyncio scaffolding lives here — the polling task, the stop Event, the
graceful-cancel-and-await-on-stop dance. The lifecycle CONTRACT itself
(start/stop/is_running) is domain.ports.sensor_port.SensorPort (Batch 3);
this is the one reusable implementation of it. Subclass and implement
`_poll()`.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from core.logging_config import logger


class BaseSensor(ABC):
    """Reusable polling sensor with start/stop lifecycle. Implements SensorPort."""

    def __init__(self, poll_interval: float = 1.5):
        self.poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name=self._sensor_name)
        logger.info(f"{self._sensor_name} started (interval={self.poll_interval}s)")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        logger.info(f"{self._sensor_name} stopped")

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def _sensor_name(self) -> str:
        return type(self).__name__

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._poll_and_publish()
            except Exception as e:
                logger.warning(f"{self._sensor_name} poll failed: {e}")
            await asyncio.sleep(self.poll_interval)

    @abstractmethod
    async def _poll_and_publish(self) -> None:
        """One polling cycle: gather events and publish them to the bus."""