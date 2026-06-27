"""SimulatedEngine: virtual-clock counting, loop-boundary swap, buffer cap."""

from __future__ import annotations

import pytest
import rig_contract

from stimpy_mcp.core.enums import EngineState, RunMode
from stimpy_mcp.core.errors import BufferTooLargeError
from stimpy_mcp.core.models import Pattern, PinMap
from stimpy_mcp.engine.simulated import SimulatedEngine

PIN_MAP = PinMap(data_pins=rig_contract.DATA_PINS, sync_pin=rig_contract.SYNC_PIN)


def _pattern(frames: list[int], rate: float = 1000.0) -> Pattern:
    return Pattern(frames=tuple(frames), sync_frames=frozenset({0}), clock_rate_hz=rate)


def _engine() -> SimulatedEngine:
    e = SimulatedEngine()
    e.configure(PIN_MAP)
    return e


def test_stage_does_not_go_live() -> None:
    e = _engine()
    e.stage_pattern(_pattern([0, 1, 2, 3]))
    s = e.status()
    assert s.running is False
    assert s.state is EngineState.IDLE
    assert s.buffer_frames == 4  # last staged is reported


def test_go_live_advances_frames() -> None:
    e = _engine()
    h = e.stage_pattern(_pattern([0, 1, 2, 3], rate=1000.0))  # tick = 1000 us = 1 ms
    e.go_live(h, mode=RunMode.REPEAT)
    assert e.status().running is True
    e.advance(0.0025)  # 2.5 frames
    s = e.status()
    assert s.frames_emitted == 2
    assert s.current_frame == 2
    assert s.loops_completed == 0
    e.advance(0.002)  # total 4.5 frames -> one loop done, into the next
    s = e.status()
    assert s.frames_emitted == 4
    assert s.loops_completed == 1
    assert s.current_frame == 0


def test_swap_happens_at_loop_boundary() -> None:
    e = _engine()
    a = e.stage_pattern(_pattern([0, 1, 2, 3], rate=1000.0))  # 4 frames, 1 ms each
    e.go_live(a, mode=RunMode.REPEAT)
    e.advance(0.0025)  # mid-cycle
    b = e.stage_pattern(_pattern([0, 1, 2, 3, 4, 5, 6, 7], rate=1000.0))  # 8 frames
    e.go_live(b, mode=RunMode.REPEAT)  # pending swap at the boundary (t=0.004)
    e.advance(0.001)  # t=0.0035, before boundary -> still buffer A
    assert e.status().buffer_frames == 4
    e.advance(0.001)  # t=0.0045, past boundary -> swapped to buffer B
    s = e.status()
    assert s.buffer_frames == 8
    assert s.current_frame == 0  # just crossed into B's frame 0


def test_once_mode_stops_after_one_pass() -> None:
    e = _engine()
    h = e.stage_pattern(_pattern([0, 1, 2, 3], rate=1000.0))
    e.go_live(h, mode=RunMode.ONCE)
    e.advance(0.0035)
    assert e.status().running is True
    e.advance(0.001)  # t=0.0045 > 4 ms -> finished one pass
    s = e.status()
    assert s.running is False
    assert s.state is EngineState.STOPPED
    assert s.frames_emitted == 4


def test_buffer_cap_rejected() -> None:
    e = _engine()
    too_big = e.limits().max_frames_per_wave + 1
    with pytest.raises(BufferTooLargeError) as exc:
        e.stage_pattern(_pattern([0] * too_big))
    assert "exceeds the engine cap" in str(exc.value)


def test_stop_drives_idle_and_counts() -> None:
    e = _engine()
    h = e.stage_pattern(_pattern([0, 1, 2, 3], rate=1000.0))
    e.go_live(h, mode=RunMode.REPEAT)
    e.advance(0.0035)
    e.stop()
    s = e.status()
    assert s.running is False
    assert s.state is EngineState.STOPPED
