"""Frozen domain models — pure data, no I/O. Importable in CI with no Pi."""

from __future__ import annotations

from dataclasses import dataclass, field

from stimpy_mcp.core.enums import EngineState


@dataclass(frozen=True, slots=True)
class PinMap:
    """BCM GPIO assignment: data_pins[i] drives channel D<i>; sync_pin is the marker."""

    data_pins: tuple[int, ...]
    sync_pin: int

    @property
    def channel_count(self) -> int:
        return len(self.data_pins)

    @property
    def all_lines(self) -> tuple[int, ...]:
        return (*self.data_pins, self.sync_pin)

    @property
    def all_mask(self) -> int:
        """32-bit BCM mask of every line this map drives (data + sync)."""
        m = 0
        for pin in self.all_lines:
            m |= 1 << pin
        return m


def clock_to_tick_us(clock_rate_hz: float) -> int:
    """Quantize a frame clock (Hz) to whole microseconds (pigpio's granularity).

    Returns the per-frame dwell in microseconds, floored at 1 us.
    """
    return max(1, round(1_000_000.0 / clock_rate_hz))


@dataclass(frozen=True, slots=True)
class Pattern:
    """A staged digital pattern: words clocked out one per tick, SYNC on sync_frames.

    Bit ``i`` of ``frames[k]`` drives channel D<i> during frame ``k``; the SYNC
    line is high for frames whose index is in ``sync_frames``.
    """

    frames: tuple[int, ...]
    sync_frames: frozenset[int]
    clock_rate_hz: float

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def tick_us(self) -> int:
        return clock_to_tick_us(self.clock_rate_hz)

    @property
    def actual_clock_rate_hz(self) -> float:
        """The achievable clock after quantization to whole-microsecond ticks."""
        return 1_000_000.0 / self.tick_us


@dataclass(frozen=True, slots=True)
class EngineLimits:
    """Hardware/daemon limits, queried at startup (never hardcoded for real pigpio)."""

    max_pulses: int
    max_cbs: int
    min_tick_us: int
    max_frames_per_wave: int


@dataclass(frozen=True, slots=True)
class StagedHandle:
    """Opaque reference to a device-side staged buffer (one or more pigpio waves)."""

    wave_ids: tuple[int, ...]
    frame_count: int
    clock_rate_hz: float
    tick_us: int


@dataclass(frozen=True, slots=True)
class EngineStatus:
    """A snapshot of the engine for the ``get_status`` tool."""

    state: EngineState
    running: bool
    clock_rate_hz: float
    actual_clock_rate_hz: float
    tick_us: int
    buffer_frames: int
    frames_emitted: int
    loops_completed: int
    current_frame: int
    limits: EngineLimits = field(
        default_factory=lambda: EngineLimits(0, 0, 1, 0)
    )
