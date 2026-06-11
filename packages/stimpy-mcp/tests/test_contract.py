"""Closes the loop with the shared rig_contract: the same PATTERN SkippyMCP asserts
on capture is the one StimpyMCP can stage."""

from __future__ import annotations

import rig_contract

from stimpy_mcp.core.models import PinMap
from stimpy_mcp.driver.stimulus import StimulusDriver
from stimpy_mcp.engine.simulated import SimulatedEngine

PIN_MAP = PinMap(data_pins=rig_contract.DATA_PINS, sync_pin=rig_contract.SYNC_PIN)


def test_pattern_has_no_adjacent_duplicates() -> None:
    p = rig_contract.PATTERN
    assert all(p[i] != p[i + 1] for i in range(len(p) - 1))
    assert p[-1] != p[0]  # wrap is also a change -> frame clock recoverable from edges


def test_frame_zero_is_alignment_marker() -> None:
    assert rig_contract.PATTERN[0] == 0x0000
    assert rig_contract.SYNC_FRAME == 0


def test_can_stage_the_canonical_vector() -> None:
    e = SimulatedEngine()
    e.configure(PIN_MAP)
    d = StimulusDriver(e, PIN_MAP, default_clock_rate_hz=1000.0)
    s = d.set_pattern(list(rig_contract.build_pattern()), go_live=False)
    assert s.buffer_frames == len(rig_contract.PATTERN)


def test_counter_frames_shape() -> None:
    assert rig_contract.counter_frames(4) == list(range(16))
    assert rig_contract.counter_sync_frames(4, 4) == [0, 4, 8, 12]
