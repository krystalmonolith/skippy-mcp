"""LgpioEngine unit tests that need no Pi and no lgpio extension.

The frame->group-word mapping is a pure function, so it is fully testable in CI.
Construction requires the lgpio extension; off-Pi it must fail with an actionable
:class:`EngineUnavailableError` (never a bare ImportError/traceback).
"""

from __future__ import annotations

import pytest

from stimpy_mcp.core.models import Pattern, PinMap
from stimpy_mcp.engine import lgpio_engine
from stimpy_mcp.engine.lgpio_engine import LgpioEngine, frames_to_group_words


def test_group_words_pack_data_and_sync() -> None:
    pin_map = PinMap(data_pins=(4, 5, 6), sync_pin=27)  # 3 channels -> SYNC is group bit 3
    pattern = Pattern(
        frames=(0b101, 0b010, 0b111), sync_frames=frozenset({0, 2}), clock_rate_hz=1000.0
    )

    words = frames_to_group_words(pin_map, pattern)

    # bits 0..2 mirror the data word; bit 3 is SYNC, set only on frames 0 and 2.
    assert words == [0b101 | 0b1000, 0b010, 0b111 | 0b1000]


def test_group_words_mask_off_high_bits() -> None:
    pin_map = PinMap(data_pins=(4, 5), sync_pin=27)  # only 2 channels
    # A word with bits above the channel count must be masked to the data lines.
    pattern = Pattern(frames=(0b11111,), sync_frames=frozenset(), clock_rate_hz=1000.0)

    assert frames_to_group_words(pin_map, pattern) == [0b11]


def test_construction_off_pi_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the "lgpio not installed" path regardless of host.
    monkeypatch.setattr(lgpio_engine, "lgpio", None)
    with pytest.raises(Exception) as exc:
        LgpioEngine(gpiochip=0)
    msg = str(exc.value)
    assert "connect" in msg and "gpiochip0" in msg and "--simulate" in msg
