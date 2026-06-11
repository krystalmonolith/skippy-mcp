"""Real GPIO engine backed by the pigpio daemon's wave API.

pigpio streams DMA control blocks to the GPIO SET/CLR registers, paced by the
PWM/PCM FIFO DREQ — hardware-timed, jitter-free, CPU-idle, and (unlike libgpiod
``set_values``) it writes the whole 17-line word in one control block, so the
prototype's ~5 us per-line staircase disappears.

``go_live`` uses ``WAVE_MODE_REPEAT_SYNC`` so a freshly staged wave swaps in at
the END of the current cycle (frame 0 = the SYNC frame): a glitch-free
double-buffer swap. v1 caps a buffer at one wave; see :class:`BufferTooLargeError`.

This module imports cleanly without pigpio installed (e.g. CI on a non-Pi host);
construction is what requires a reachable ``pigpiod``.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from stimpy_mcp.core.enums import EngineState, RunMode
from stimpy_mcp.core.errors import BufferTooLargeError, EngineStateError, EngineUnavailableError
from stimpy_mcp.core.models import (
    EngineLimits,
    EngineStatus,
    Pattern,
    PinMap,
    StagedHandle,
)

try:  # pigpio is only present on the Pi; keep the module importable everywhere.
    import pigpio
except ImportError:  # pragma: no cover - exercised only off-Pi
    pigpio = None

#: pigpio generates roughly this many DMA control blocks per generic pulse;
#: used only to derive an advisory max_frames_per_wave (stage_pattern is authoritative).
_CBS_PER_PULSE = 2


class PigpioEngine:
    """A :class:`~stimpy_mcp.engine.base.StimulusEngine` over a local ``pigpiod``."""

    def __init__(self, host: str = "localhost", port: int = 8888) -> None:
        if pigpio is None:
            raise EngineUnavailableError(host=host, port=port, reason="pigpio module not installed")
        self._pi = pigpio.pi(host, port)
        if not self._pi.connected:
            raise EngineUnavailableError(host=host, port=port, reason="daemon not reachable")
        self._pin_map: PinMap | None = None
        self._live_wid: int | None = None
        self._retired: set[int] = set()
        self._last_staged: StagedHandle | None = None
        self._state = EngineState.IDLE
        self._mode = RunMode.REPEAT
        self._run_started = 0.0  # time.monotonic() at last go_live-from-idle

    # -- StimulusEngine ---------------------------------------------------
    def configure(self, pin_map: PinMap) -> None:
        self._pin_map = pin_map
        for pin in pin_map.all_lines:
            self._pi.set_mode(pin, pigpio.OUTPUT)
            self._pi.write(pin, 0)
        self._state = EngineState.IDLE

    def stage_pattern(self, pattern: Pattern) -> StagedHandle:
        if self._pin_map is None:
            raise EngineStateError(
                "set_pattern",
                reason="engine not configured with a pin map",
                check="this is an internal ordering bug; configure() must run first",
            )
        max_frames = self.limits().max_frames_per_wave
        if pattern.frame_count > max_frames:
            raise BufferTooLargeError(frames=pattern.frame_count, max_frames=max_frames)

        pulses = self._build_pulses(pattern)
        self._pi.wave_add_new()  # clear the pulse staging buffer (not created waves)
        self._pi.wave_add_generic(pulses)
        wid = self._pi.wave_create()
        if wid < 0:  # pigpio signals over-capacity / empty wave with a negative id
            raise BufferTooLargeError(frames=pattern.frame_count, max_frames=max_frames)
        handle = StagedHandle(
            wave_ids=(wid,),
            frame_count=pattern.frame_count,
            clock_rate_hz=pattern.clock_rate_hz,
            tick_us=pattern.tick_us,
        )
        self._last_staged = handle
        return handle

    def go_live(self, handle: StagedHandle, *, mode: RunMode) -> None:
        self._gc()
        wid = handle.wave_ids[0]
        pi_mode = (
            pigpio.WAVE_MODE_REPEAT_SYNC if mode is RunMode.REPEAT
            else pigpio.WAVE_MODE_ONE_SHOT_SYNC
        )
        was_running = self._state is EngineState.RUNNING and self._live_wid is not None
        self._pi.wave_send_using_mode(wid, pi_mode)
        if self._live_wid is not None and self._live_wid != wid:
            self._retired.add(self._live_wid)  # delete it once the swap completes
        self._live_wid = wid
        self._mode = mode
        self._state = EngineState.RUNNING
        if not was_running:
            self._run_started = time.monotonic()

    def stop(self) -> None:
        self._pi.wave_tx_stop()
        if self._pin_map is not None:
            for pin in self._pin_map.all_lines:
                self._pi.write(pin, 0)
        self._gc(force=True)
        self._live_wid = None
        self._state = EngineState.STOPPED

    def status(self) -> EngineStatus:
        self._gc()
        limits = self.limits()
        live = self._last_staged if self._state is EngineState.RUNNING else None
        running = bool(self._pi.wave_tx_busy())
        if not running or live is None:
            idle_or_stopped = (
                EngineState.STOPPED if self._state is EngineState.STOPPED else EngineState.IDLE
            )
            return EngineStatus(
                state=idle_or_stopped,
                running=False,
                clock_rate_hz=0.0,
                actual_clock_rate_hz=0.0,
                tick_us=0,
                buffer_frames=self._last_staged.frame_count if self._last_staged else 0,
                frames_emitted=0,
                loops_completed=0,
                current_frame=0,
                limits=limits,
            )
        # pigpio exposes no exact frame position; derive from host elapsed time.
        tick_s = live.tick_us / 1_000_000.0
        seg = max(0, int((time.monotonic() - self._run_started) / tick_s))
        fc = live.frame_count
        return EngineStatus(
            state=EngineState.RUNNING,
            running=True,
            clock_rate_hz=live.clock_rate_hz,
            actual_clock_rate_hz=1_000_000.0 / live.tick_us,
            tick_us=live.tick_us,
            buffer_frames=fc,
            frames_emitted=seg,           # approximate (host clock, not DMA)
            loops_completed=seg // fc,
            current_frame=seg % fc,
            limits=limits,
        )

    def limits(self) -> EngineLimits:
        max_pulses = int(self._pi.wave_get_max_pulses())
        max_cbs = int(self._pi.wave_get_max_cbs())
        max_frames = min(max_pulses, max_cbs // _CBS_PER_PULSE)
        return EngineLimits(
            max_pulses=max_pulses,
            max_cbs=max_cbs,
            min_tick_us=1,
            max_frames_per_wave=max_frames,
        )

    def close(self) -> None:
        try:
            self._pi.wave_tx_stop()
            self._pi.wave_clear()
        finally:
            self._pi.stop()
        self._state = EngineState.IDLE

    # -- internals --------------------------------------------------------
    def _build_pulses(self, pattern: Pattern) -> list[Any]:
        """Map each frame word to one pigpio pulse (on-mask, off-mask, tick).

        Every frame fully defines all 17 lines: SET the high lines, CLEAR the
        rest. pigpio applies both masks within one DMA control block, so the
        whole word changes simultaneously at the frame boundary.
        """
        assert self._pin_map is not None
        pm = self._pin_map
        all_mask = pm.all_mask
        tick = pattern.tick_us
        pulses = []
        for i, word in enumerate(pattern.frames):
            on_mask = 0
            for bit, pin in enumerate(pm.data_pins):
                if word >> bit & 1:
                    on_mask |= 1 << pin
            if i in pattern.sync_frames:
                on_mask |= 1 << pm.sync_pin
            off_mask = all_mask & ~on_mask
            pulses.append(pigpio.pulse(on_mask, off_mask, tick))
        return pulses

    def _gc(self, *, force: bool = False) -> None:
        """Delete retired waves once they are no longer the one transmitting."""
        if not self._retired:
            return
        current = -1 if force else int(self._pi.wave_tx_at())
        for wid in list(self._retired):
            if force or wid != current:
                with contextlib.suppress(Exception):  # best-effort cleanup
                    self._pi.wave_delete(wid)
                self._retired.discard(wid)
