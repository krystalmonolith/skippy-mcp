"""StimulusDriver: validation + staging against the simulator."""

from __future__ import annotations

import pytest
import rig_contract

from stimpy_mcp.core.errors import EngineStateError, ValidationError
from stimpy_mcp.core.models import PinMap
from stimpy_mcp.driver.stimulus import StimulusDriver
from stimpy_mcp.engine.simulated import SimulatedEngine

PIN_MAP = PinMap(data_pins=rig_contract.DATA_PINS, sync_pin=rig_contract.SYNC_PIN)


def _driver() -> StimulusDriver:
    e = SimulatedEngine()
    e.configure(PIN_MAP)
    return StimulusDriver(e, PIN_MAP, default_clock_rate_hz=1000.0)


def test_set_pattern_goes_live_by_default() -> None:
    d = _driver()
    s = d.set_pattern([0, 1, 2, 3])
    assert s.running is True
    assert s.buffer_frames == 4


def test_set_pattern_staged_only() -> None:
    d = _driver()
    s = d.set_pattern([0, 1, 2, 3], go_live=False)
    assert s.running is False


def test_start_without_pattern_errors() -> None:
    d = _driver()
    with pytest.raises(EngineStateError):
        d.start()


def test_reject_word_out_of_range() -> None:
    d = _driver()
    with pytest.raises(ValidationError) as exc:
        d.set_pattern([0, 0x1_0000])  # 17 bits, exceeds 16-channel word
    assert "frames[1]" in str(exc.value)


def test_reject_empty_pattern() -> None:
    d = _driver()
    with pytest.raises(ValidationError):
        d.set_pattern([])


def test_reject_bad_sync_frame() -> None:
    d = _driver()
    with pytest.raises(ValidationError):
        d.set_pattern([0, 1, 2], sync_frames=[5])  # out of range for 3 frames


def test_reject_nonpositive_clock() -> None:
    d = _driver()
    with pytest.raises(ValidationError):
        d.set_clock_rate(0)


def test_set_clock_rate_applies_to_next_pattern() -> None:
    d = _driver()
    d.set_clock_rate(3000.0)  # 3000 Hz -> tick = round(333.33) = 333 us
    s = d.set_pattern([0, 1, 2, 3])  # uses the new default clock
    assert s.tick_us == 333
    assert abs(s.actual_clock_rate_hz - 1_000_000.0 / 333) < 1e-6


def test_stop_is_idempotent() -> None:
    d = _driver()
    d.set_pattern([0, 1, 2, 3])
    d.stop()
    s = d.stop()
    assert s.running is False
