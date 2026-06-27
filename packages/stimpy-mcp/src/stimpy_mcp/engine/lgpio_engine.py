"""Real GPIO engine backed by the Linux kernel GPIO character device via lgpio.

Unlike pigpio (DMA wave hardware, now deprecated and Pi-5/RP1-incompatible), lgpio
has **no hardware wave engine**. This engine clocks the pattern out from a dedicated
*pacing thread* that writes the whole data+SYNC word atomically with one lgpio
``group_write`` per frame -- so every line changes together at the frame boundary
(no per-line staircase). Pacing is a monotonic software schedule (sleep, then spin
the final few hundred microseconds), best-effort raised to ``SCHED_FIFO``; it is
*not* DMA, so expect host-scheduler jitter at very high clock rates. Keep the frame
clock modest (a few kHz) for clean edges.

``go_live`` swaps a freshly staged buffer in at the next loop boundary (frame 0 =
the SYNC frame): the pacing thread finishes the current cycle, then begins the new
buffer at frame 0 -- a glitch-free double-buffer swap, the software analogue of
pigpio's ``WAVE_MODE_REPEAT_SYNC``.

The module imports cleanly without lgpio (e.g. CI on a non-Pi host); construction
is what requires ``/dev/gpiochip*`` and the lgpio extension.
"""

from __future__ import annotations

import threading
import time
from contextlib import suppress

from stimpy_mcp.core.enums import EngineState, RunMode
from stimpy_mcp.core.errors import BufferTooLargeError, EngineStateError, EngineUnavailableError
from stimpy_mcp.core.models import (
    EngineLimits,
    EngineStatus,
    Pattern,
    PinMap,
    StagedHandle,
)

try:  # lgpio is only present on the Pi; keep the module importable everywhere.
    import lgpio
except ImportError:  # pragma: no cover - exercised only off-Pi
    lgpio = None

#: Software buffer cap (memory-bound; no DMA control-block limits like pigpio).
_MAX_FRAMES = 100_000
#: Below this per-frame dwell the software pacer cannot hold edges cleanly.
_MIN_TICK_US = 20
#: Busy-wait the final stretch to each frame deadline for low jitter.
_SPIN_GUARD_S = 300e-6


def frames_to_group_words(pin_map: PinMap, pattern: Pattern) -> list[int]:
    """Precompute one lgpio group word per frame (pure function; no hardware).

    The group is claimed in ``pin_map.all_lines`` order, so group bit ``i`` drives
    ``all_lines[i]``: bits ``0..N-1`` are the data channels D0..D<N-1> (identical to
    the pattern word's low bits) and bit ``N`` is SYNC, set on frames in
    ``pattern.sync_frames``.
    """
    n = pin_map.channel_count
    data_mask = (1 << n) - 1
    sync_bit = 1 << n
    words: list[int] = []
    for i, word in enumerate(pattern.frames):
        bits = word & data_mask
        if i in pattern.sync_frames:
            bits |= sync_bit
        words.append(bits)
    return words


def _try_realtime() -> None:
    """Best-effort: raise the calling thread to SCHED_FIFO; ignore if not permitted."""
    with suppress(Exception):
        import os

        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(10))


class LgpioEngine:
    """A :class:`~stimpy_mcp.engine.base.StimulusEngine` over ``/dev/gpiochip`` via lgpio."""

    def __init__(self, gpiochip: int = 0) -> None:
        if lgpio is None:
            raise EngineUnavailableError(gpiochip=gpiochip, reason="lgpio module not installed")
        try:
            self._h = lgpio.gpiochip_open(gpiochip)
        except Exception as exc:  # lgpio.error / OSError
            raise EngineUnavailableError(
                gpiochip=gpiochip, reason=f"cannot open the GPIO chardev: {exc}"
            ) from exc
        self._gpiochip = gpiochip
        self._pin_map: PinMap | None = None
        self._leader: int | None = None
        self._mask = 0

        # Shared state below is guarded by self._cv (a Condition over an RLock).
        self._cv = threading.Condition()
        self._live_words: list[int] | None = None
        self._live_handle: StagedHandle | None = None
        self._pending_words: list[int] | None = None
        self._pending_handle: StagedHandle | None = None
        self._mode = RunMode.REPEAT
        self._pending_mode = RunMode.REPEAT
        self._current_frame = 0
        self._frames_emitted = 0
        self._loops_completed = 0
        self._state = EngineState.IDLE
        self._last_staged: StagedHandle | None = None
        self._staged_words: dict[int, list[int]] = {}
        self._next_wave_id = 0
        self._closing = False

        self._worker = threading.Thread(target=self._run, name="lgpio-pacer", daemon=True)
        self._worker.start()

    # -- StimulusEngine ---------------------------------------------------
    def configure(self, pin_map: PinMap) -> None:
        with self._cv:
            if self._leader is not None:
                with suppress(Exception):
                    lgpio.group_free(self._h, self._leader)
            lines = list(pin_map.all_lines)
            try:
                lgpio.group_claim_output(self._h, lines, [0] * len(lines))
            except Exception as exc:
                raise EngineUnavailableError(
                    gpiochip=self._gpiochip, reason=f"cannot claim GPIO lines {lines}: {exc}"
                ) from exc
            self._pin_map = pin_map
            self._leader = lines[0]
            self._mask = (1 << len(lines)) - 1
            self._state = EngineState.IDLE

    def stage_pattern(self, pattern: Pattern) -> StagedHandle:
        if self._pin_map is None:
            raise EngineStateError(
                "set_pattern",
                reason="engine not configured with a pin map",
                check="this is an internal ordering bug; configure() must run first",
            )
        if pattern.frame_count > _MAX_FRAMES:
            raise BufferTooLargeError(frames=pattern.frame_count, max_frames=_MAX_FRAMES)
        words = frames_to_group_words(self._pin_map, pattern)
        with self._cv:
            wid = self._next_wave_id
            self._next_wave_id += 1
            self._staged_words[wid] = words
            handle = StagedHandle(
                wave_ids=(wid,),
                frame_count=pattern.frame_count,
                clock_rate_hz=pattern.clock_rate_hz,
                tick_us=pattern.tick_us,
            )
            self._last_staged = handle
            return handle

    def go_live(self, handle: StagedHandle, *, mode: RunMode) -> None:
        with self._cv:
            words = self._staged_words.get(handle.wave_ids[0])
            if words is None:
                raise EngineStateError(
                    "start",
                    reason="the staged buffer is no longer available",
                    check="call set_pattern again before start/go_live",
                )
            if self._state is EngineState.RUNNING and self._live_words is not None:
                # Defer to the next loop boundary (frame 0) -- glitch-free swap.
                self._pending_words = words
                self._pending_handle = handle
                self._pending_mode = mode
            else:
                self._retire_except(handle.wave_ids[0])
                self._live_words = words
                self._live_handle = handle
                self._pending_words = None
                self._pending_handle = None
                self._mode = mode
                self._current_frame = 0
                self._state = EngineState.RUNNING
            self._cv.notify_all()

    def stop(self) -> None:
        with self._cv:
            self._live_words = None
            self._live_handle = None
            self._pending_words = None
            self._pending_handle = None
            self._state = EngineState.STOPPED
            self._current_frame = 0
            self._drive_low_locked()
            self._cv.notify_all()

    def status(self) -> EngineStatus:
        with self._cv:
            limits = self.limits()
            live = self._live_handle
            if live is None or self._live_words is None:
                return EngineStatus(
                    state=self._state,
                    running=False,
                    clock_rate_hz=0.0,
                    actual_clock_rate_hz=0.0,
                    tick_us=0,
                    buffer_frames=self._last_staged.frame_count if self._last_staged else 0,
                    frames_emitted=self._frames_emitted,
                    loops_completed=self._loops_completed,
                    current_frame=0,
                    limits=limits,
                )
            return EngineStatus(
                state=EngineState.RUNNING,
                running=True,
                clock_rate_hz=live.clock_rate_hz,
                actual_clock_rate_hz=1_000_000.0 / live.tick_us,
                tick_us=live.tick_us,
                buffer_frames=live.frame_count,
                frames_emitted=self._frames_emitted,
                loops_completed=self._loops_completed,
                current_frame=self._current_frame,
                limits=limits,
            )

    def limits(self) -> EngineLimits:
        # No DMA pulse/CB ceilings; report the software buffer cap and pacer floor.
        return EngineLimits(
            max_pulses=_MAX_FRAMES,
            max_cbs=_MAX_FRAMES,
            min_tick_us=_MIN_TICK_US,
            max_frames_per_wave=_MAX_FRAMES,
        )

    def close(self) -> None:
        with self._cv:
            self._closing = True
            self._live_words = None
            self._pending_words = None
            self._cv.notify_all()
        self._worker.join(timeout=2.0)
        with suppress(Exception):
            if self._leader is not None:
                self._drive_low_locked()
                lgpio.group_free(self._h, self._leader)
        with suppress(Exception):
            lgpio.gpiochip_close(self._h)
        self._state = EngineState.IDLE

    # -- internals --------------------------------------------------------
    def _drive_low_locked(self) -> None:
        if self._leader is not None:
            with suppress(Exception):
                lgpio.group_write(self._h, self._leader, 0, self._mask)

    def _retire_except(self, keep: int) -> None:
        for wid in list(self._staged_words):
            if wid != keep:
                del self._staged_words[wid]

    def _pace_until(self, deadline: float) -> None:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > _SPIN_GUARD_S:
            time.sleep(remaining - _SPIN_GUARD_S)
        while time.perf_counter() < deadline:
            pass

    def _run(self) -> None:
        """Pacing thread: emit one group word per tick; swap/stop at loop boundaries."""
        _try_realtime()
        next_t = time.perf_counter()
        while True:
            with self._cv:
                while self._live_words is None and not self._closing:
                    self._cv.wait()
                    next_t = time.perf_counter()  # reset the schedule on resume
                if self._closing:
                    return
                # Apply a deferred swap exactly at the loop boundary (frame 0).
                if self._current_frame == 0 and self._pending_words is not None:
                    old = self._live_handle
                    self._live_words = self._pending_words
                    self._live_handle = self._pending_handle
                    self._mode = self._pending_mode
                    self._pending_words = None
                    self._pending_handle = None
                    if old is not None:
                        self._staged_words.pop(old.wave_ids[0], None)
                words = self._live_words
                handle = self._live_handle
                assert words is not None and handle is not None
                idx = self._current_frame
                tick_s = handle.tick_us / 1_000_000.0
                wcount = len(words)
                bits = words[idx]
                h, leader, mask = self._h, self._leader, self._mask

            if leader is not None:
                with suppress(Exception):
                    lgpio.group_write(h, leader, bits, mask)
            next_t += tick_s
            self._pace_until(next_t)

            with self._cv:
                if self._closing:
                    return
                if self._live_words is None:  # stopped while pacing
                    next_t = time.perf_counter()
                    continue
                self._frames_emitted += 1
                nidx = idx + 1
                if nidx >= wcount:
                    nidx = 0
                    self._loops_completed += 1
                    if self._mode is RunMode.ONCE and self._pending_words is None:
                        self._live_words = None
                        self._live_handle = None
                        self._state = EngineState.STOPPED
                        self._current_frame = 0
                        self._drive_low_locked()
                        continue
                self._current_frame = nidx
