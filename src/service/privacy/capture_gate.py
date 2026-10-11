"""CaptureGate — the single source of truth for whether the microphone is live.

★ New, PS-mandatory (REQ-M-04/M-05). Referenced by five port docstrings
(`audio_capture_port`, `indicator_port`, `mute_switch_port`,
`gpio_indicator`, `ambient_loop`) as the enforcement point, but never
actually built during Batches 1-11 — this file closes that gap.

Per ADR-003 ("one source of truth, so the gate and the LED cannot
diverge"), every capture-gated consumer reacts to a transition that
originates HERE, not to the raw MuteSwitchPort event and not to UI state:

    MuteSwitchPort ──raises──> CaptureGate ──fans out──> AudioCapturePort (stop/start)
                                          ├────────────> IndicatorPort   (LED)
                                          ├────────────> StatusDisplayPort (text, optional)
                                          └────────────> EventPublisherPort (UI/ambient feed)

Fail-closed rule: if the indicator cannot be driven, the gate forces
itself MUTED rather than listening with a light that may be lying. An
indicator that can't show "listening" must not be allowed to permit
listening — that is the whole point of REQ-M-05, and treating an
indicator failure as benign would quietly defeat it.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone

from core.logging_config import logger
from domain.entities.capture_state import CaptureState
from domain.events.ambient_event import AmbientEvent
from domain.events.capture_event import CaptureEvent
from domain.events.event_kind import EventKind
from domain.ports.audio_capture_port import AudioCapturePort
from domain.ports.event_publisher_port import EventPublisherPort
from domain.ports.indicator_port import IndicatorPort
from domain.ports.mute_switch_port import MuteSwitchPort
from domain.ports.status_display_port import StatusDisplayPort
from domain.value_objects.urgency import Urgency


class CaptureGate:
    """Owns CaptureState; gates the mic and drives the indicator from one event."""

    def __init__(
        self,
        mute_switch: MuteSwitchPort,
        *,
        indicator: IndicatorPort | None = None,
        audio: AudioCapturePort | None = None,
        bus: EventPublisherPort | None = None,
        status_display: StatusDisplayPort | None = None,
        start_muted: bool = True,
    ):
        self._mute_switch = mute_switch
        self._indicator = indicator
        self._audio = audio
        self._bus = bus
        self._status_display = status_display
        self._capture_paused = False
        self._capture_lifecycle_lock = threading.RLock()
        # Guards _state against the mute-switch callback arriving on a
        # foreign thread (pynput's listener thread) while a reader is in
        # is_muted() on the event loop thread.
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._state = CaptureState(
            muted=start_muted,
            mute_source=mute_switch.source_name,
            since=datetime.now(timezone.utc),
        )

    # -- Lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Subscribe to the switch and sync hardware to its real current state."""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None  # started outside asyncio; bus publishes become no-ops

        self._mute_switch.subscribe(self._on_switch_change)

        # The switch is authoritative at startup — adopt whatever it reports
        # rather than assuming our constructor default was right.
        actual = self._mute_switch.is_muted()
        self._apply(muted=actual, source=self._mute_switch.source_name, previous=self._state.muted)
        logger.info(f"[capture-gate] started; muted={self._state.muted} source={self._state.mute_source!r}")

    def stop(self) -> None:
        """Mute and release the mic. Always safe to call twice."""
        with self._capture_lifecycle_lock:
            self._capture_paused = False
            self._apply(muted=True, source="shutdown", previous=self._state.muted)
        stopper = getattr(self._mute_switch, "stop", None)
        if callable(stopper):
            try:
                stopper()
            except Exception as e:
                logger.warning(f"[capture-gate] mute switch failed to stop: {e}")
        logger.info("[capture-gate] stopped (muted, mic released)")

    # -- Queries ----------------------------------------------------------

    def is_muted(self) -> bool:
        """What AmbientLoop checks every tick before reading the mic."""
        with self._lock:
            return self._state.muted

    def pause_capture(self, source: str = "voice_turn") -> None:
        """Stop audio capture temporarily without changing the user's mute state."""
        with self._capture_lifecycle_lock:
            if self._capture_paused:
                return
            self._capture_paused = True
            if not self.is_muted():
                self._stop_audio()
            logger.info(f"[capture-gate] capture paused (source={source!r})")

    def resume_capture(self, source: str = "voice_turn") -> bool:
        """Resume capture unless the user has muted the microphone meanwhile."""
        with self._capture_lifecycle_lock:
            if not self._capture_paused:
                return not self.is_muted()
            self._capture_paused = False
            muted = self.is_muted()
            if not muted:
                self._start_audio()
            logger.info(
                f"[capture-gate] capture {'remains stopped' if muted else 'resumed'} "
                f"(source={source!r})"
            )
            return not muted

    @property
    def state(self) -> CaptureState:
        with self._lock:
            return self._state

    # -- Transitions ------------------------------------------------------

    def set_muted(self, muted: bool, source: str = "api") -> CaptureState:
        """Software mute path (UI / API / CLI). Same code path as hardware."""
        previous = self.is_muted()
        # A physical switch in the muted position wins: software may not unmute over it.
        forced = getattr(self._mute_switch, "is_forced_muted", None)
        if not muted and callable(forced) and forced():
            logger.warning(f"[capture-gate] unmute from {source!r} refused: hardware switch is in the MUTED position")
            return self.state
        self._apply(muted=muted, source=source, previous=previous)
        # Keep an input device's toggle baseline aligned so a later hotkey
        # press toggles from the gate's real state — the gate stays the one
        # source of truth; the switch is only told, never asked.
        sync = getattr(self._mute_switch, "sync_state", None)
        if callable(sync):
            try:
                sync(muted)
            except Exception as e:
                logger.warning(f"[capture-gate] mute switch sync_state failed: {e}")
        return self.state

    def _on_switch_change(self, event: CaptureEvent) -> None:
        """MuteSwitchPort callback. May arrive on a non-asyncio thread."""
        self._apply(muted=event.muted, source=event.source, previous=event.previous_muted)

    def _apply(self, *, muted: bool, source: str, previous: bool) -> None:
        """The one place capture state changes. Ordering here is deliberate."""
        # Unmuting: prove the indicator works BEFORE opening the mic, so we
        # can never be live with a dark light. Muting: close the mic FIRST,
        # then update the light — on the way down, stopping capture is the
        # guarantee that matters most.
        with self._capture_lifecycle_lock:
            if not muted:
                if not self._drive_indicator(False):
                    logger.error("[capture-gate] indicator failed on unmute — staying MUTED (fail-closed)")
                    self._drive_indicator(True)
                    self._commit(muted=True, source=f"{source}:indicator-failed", previous=previous)
                    return
                if not self._capture_paused:
                    self._start_audio()
            else:
                self._stop_audio()
                self._drive_indicator(True)

            self._commit(muted=muted, source=source, previous=previous)

    def _commit(self, *, muted: bool, source: str, previous: bool) -> None:
        with self._lock:
            self._state = CaptureState(muted=muted, mute_source=source, since=datetime.now(timezone.utc))

        self._show_status(muted)
        if muted != previous:
            logger.info(f"[capture-gate] {'MUTED' if muted else 'LISTENING'} (source={source!r})")
            self._publish(muted=muted, previous=previous, source=source)

    # -- Fan-out (each isolated; one failing consumer must not block others) --

    def _drive_indicator(self, muted: bool) -> bool:
        if self._indicator is None:
            return True
        try:
            self._indicator.set_muted(muted)
            return True
        except Exception as e:
            logger.error(f"[capture-gate] indicator.set_muted({muted}) failed: {e}")
            return False

    def _start_audio(self) -> None:
        if self._audio is None:
            return
        try:
            self._audio.start()
        except Exception as e:
            logger.error(f"[capture-gate] audio.start() failed: {e}")

    def _stop_audio(self) -> None:
        if self._audio is None:
            return
        try:
            self._audio.stop()
        except Exception as e:
            logger.warning(f"[capture-gate] audio.stop() failed: {e}")

    def _show_status(self, muted: bool) -> None:
        if self._status_display is None:
            return
        try:
            self._status_display.show(("MUTED" if muted else "LISTENING",))
        except Exception as e:
            logger.warning(f"[capture-gate] status display failed: {e}")

    def _publish(self, *, muted: bool, previous: bool, source: str) -> None:
        """Surface the transition on the ambient feed so the UI can react."""
        if self._bus is None or self._loop is None:
            return
        event = AmbientEvent(
            kind=EventKind.SYSTEM,
            description="Microphone muted." if muted else "Microphone live.",
            urgency=Urgency.NORMAL,
            source="capture_gate",
            dedupe_key=f"capture:{'muted' if muted else 'live'}",
            payload={"muted": muted, "previous_muted": previous, "mute_source": source},
        )
        try:
            # The callback may be on pynput's thread, so hop to the loop
            # thread rather than assuming we're already on it.
            asyncio.run_coroutine_threadsafe(self._bus.publish(event), self._loop)
        except Exception as e:
            logger.warning(f"[capture-gate] failed to publish capture event: {e}")