"""In-memory simulator engine — first-class, deterministic, no hardware.

Models pigpio's behaviour (including the REPEAT_SYNC swap-at-loop-boundary) so
the driver, MCP, and tool layers run in CI with no Pi and no daemon. A *virtual
clock* (advanced by :meth:`advance`) drives the emitted-frame counter, so tests
assert "the staged buffer goes live on the next sync frame" deterministically.
"""

from __future__ import annotations

import math

from stimpy_mcp.core.enums import EngineState, RunMode
from stimpy_mcp.core.errors import BufferTooLargeError
from stimpy_mcp.core.models import (
    EngineLimits,
    EngineStatus,
    Pattern,
    PinMap,
    StagedHandle,
)

# Realistic pigpio numbers so the buffer-cap path is exercised in CI.
_SIM_MAX_PULSES = 12000
_SIM_MAX_CBS = 25600


class SimulatedEngine:
    """A fake :class:`~stimpy_mcp.engine.base.StimulusEngine` backed by a virtual clock."""

    def __init__(self) -> None:
        self.history: list[tuple[str, object]] = []
        self._pin_map: PinMap | None = None
        self._next_wave_id = 0

        self._state = EngineState.IDLE
        self._mode = RunMode.REPEAT
        self._live: StagedHandle | None = None
        self._pending: StagedHandle | None = None
        self._last_staged: StagedHandle | None = None
        self._live_start = 0.0       # virtual time of the current live cycle's frame 0
        self._pending_boundary = 0.0  # virtual time the pending swap takes effect
        self._emitted_base = 0       # frames emitted in prior (already-swapped) segments
        self._now = 0.0              # virtual seconds
        self._lines_high = False     # whether any line is currently driven high

    # -- test helpers -----------------------------------------------------
    def advance(self, seconds: float) -> None:
        """Advance the virtual clock by ``seconds`` (test helper)."""
        if seconds < 0:
            raise ValueError("cannot advance virtual time backwards")
        self._now += seconds
        self._settle()

    def set_virtual_time(self, seconds: float) -> None:
        """Set absolute virtual time (test helper)."""
        self._now = seconds
        self._settle()

    # -- StimulusEngine ---------------------------------------------------
    def configure(self, pin_map: PinMap) -> None:
        self._pin_map = pin_map
        self.history.append(("configure", pin_map))
        self._state = EngineState.IDLE

    def stage_pattern(self, pattern: Pattern) -> StagedHandle:
        max_frames = self.limits().max_frames_per_wave
        if pattern.frame_count > max_frames:
            raise BufferTooLargeError(frames=pattern.frame_count, max_frames=max_frames)
        wid = self._next_wave_id
        self._next_wave_id += 1
        handle = StagedHandle(
            wave_ids=(wid,),
            frame_count=pattern.frame_count,
            clock_rate_hz=pattern.clock_rate_hz,
            tick_us=pattern.tick_us,
        )
        self._last_staged = handle
        self.history.append(("stage_pattern", handle))
        return handle

    def go_live(self, handle: StagedHandle, *, mode: RunMode) -> None:
        self._settle()
        self._mode = mode
        self.history.append(("go_live", (handle, mode)))
        if self._state is EngineState.RUNNING and self._live is not None:
            # Defer to the next loop boundary (glitch-free REPEAT_SYNC swap). Lock
            # that boundary time NOW so later settles don't slide it forward.
            fc = self._live.frame_count
            tick_s = self._live.tick_us / 1_000_000.0
            loops_done = self._segment_frames() // fc
            self._pending = handle
            self._pending_boundary = self._live_start + (loops_done + 1) * fc * tick_s
        else:
            self._live = handle
            self._pending = None
            self._live_start = self._now
            self._state = EngineState.RUNNING
            self._lines_high = True

    def stop(self) -> None:
        self._settle()
        if self._live is not None:
            self._emitted_base += self._segment_frames()
        self._live = None
        self._pending = None
        self._state = EngineState.STOPPED
        self._lines_high = False
        self.history.append(("stop", None))

    def status(self) -> EngineStatus:
        self._settle()
        limits = self.limits()
        if self._live is None:
            return EngineStatus(
                state=self._state,
                running=False,
                clock_rate_hz=0.0,
                actual_clock_rate_hz=0.0,
                tick_us=0,
                buffer_frames=self._last_staged.frame_count if self._last_staged else 0,
                frames_emitted=self._emitted_base,
                loops_completed=0,
                current_frame=0,
                limits=limits,
            )
        seg = self._segment_frames()
        fc = self._live.frame_count
        actual = 1_000_000.0 / self._live.tick_us
        return EngineStatus(
            state=EngineState.RUNNING,
            running=True,
            clock_rate_hz=self._live.clock_rate_hz,
            actual_clock_rate_hz=actual,
            tick_us=self._live.tick_us,
            buffer_frames=fc,
            frames_emitted=self._emitted_base + seg,
            loops_completed=seg // fc,
            current_frame=seg % fc,
            limits=limits,
        )

    def limits(self) -> EngineLimits:
        # One generic pulse ~ 2 CBs in pigpio; mirror the real engine's derivation.
        max_frames = min(_SIM_MAX_PULSES, _SIM_MAX_CBS // 2)
        return EngineLimits(
            max_pulses=_SIM_MAX_PULSES,
            max_cbs=_SIM_MAX_CBS,
            min_tick_us=1,
            max_frames_per_wave=max_frames,
        )

    def close(self) -> None:
        self._live = None
        self._pending = None
        self._state = EngineState.IDLE
        self._lines_high = False
        self.history.append(("close", None))

    # -- internals --------------------------------------------------------
    def _segment_frames(self) -> int:
        """Frames clocked out since the current live cycle's frame 0."""
        if self._live is None:
            return 0
        tick_s = self._live.tick_us / 1_000_000.0
        return max(0, math.floor((self._now - self._live_start) / tick_s))

    def _settle(self) -> None:
        """Advance state to ``self._now``: apply pending swaps and ONCE-stop."""
        while self._live is not None:
            fc = self._live.frame_count
            tick_s = self._live.tick_us / 1_000_000.0

            if self._pending is not None and self._now >= self._pending_boundary:
                frames_to_boundary = round((self._pending_boundary - self._live_start) / tick_s)
                self._emitted_base += frames_to_boundary
                self._live = self._pending
                self._pending = None
                self._live_start = self._pending_boundary
                continue  # re-evaluate with the new live buffer

            finished_pass = self._pending is None and self._segment_frames() >= fc
            if self._mode is RunMode.ONCE and finished_pass:
                # Finished one pass; stop at the end of the buffer.
                self._emitted_base += fc
                self._live = None
                self._state = EngineState.STOPPED
                self._lines_high = False
                return
            break
