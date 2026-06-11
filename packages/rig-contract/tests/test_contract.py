"""Contract invariants for the shared rig_contract package.

These assert the pin map and PATTERN the rig is built on, with no dependency on
either server — rig_contract is the single source of truth both import.
"""

from __future__ import annotations

import pytest

import rig_contract


def test_pin_map_shape() -> None:
    assert rig_contract.CHANNEL_COUNT == 16
    assert len(rig_contract.DATA_PINS) == rig_contract.CHANNEL_COUNT
    assert rig_contract.MASK == 0xFFFF
    # Every line (16 data + SYNC) is a distinct BCM offset.
    lines = (*rig_contract.DATA_PINS, rig_contract.SYNC_PIN)
    assert len(set(lines)) == len(lines)


def test_frame_zero_is_alignment_marker() -> None:
    assert rig_contract.SYNC_FRAME == 0
    assert rig_contract.PATTERN[0] == 0x0000


def test_pattern_words_fit_the_channel_mask() -> None:
    assert all(0 <= word <= rig_contract.MASK for word in rig_contract.PATTERN)


def test_pattern_has_no_adjacent_duplicates() -> None:
    p = rig_contract.PATTERN
    assert all(p[i] != p[i + 1] for i in range(len(p) - 1))
    assert p[-1] != p[0]  # wrap is also a change -> frame clock recoverable from edges


def test_build_pattern_matches_pattern_constant() -> None:
    assert tuple(rig_contract.build_pattern()) == rig_contract.PATTERN
    # build_pattern() returns a fresh, independent list each call.
    a = rig_contract.build_pattern()
    a[0] = 0xDEAD
    assert rig_contract.build_pattern()[0] == 0x0000


def test_counter_frames_shape() -> None:
    assert rig_contract.counter_frames(4) == list(range(16))
    assert rig_contract.counter_frames(1) == [0, 1]


def test_counter_frames_rejects_out_of_range_bits() -> None:
    with pytest.raises(ValueError, match="bits"):
        rig_contract.counter_frames(0)
    with pytest.raises(ValueError, match="bits"):
        rig_contract.counter_frames(rig_contract.CHANNEL_COUNT + 1)


def test_counter_sync_frames() -> None:
    assert rig_contract.counter_sync_frames(4, 4) == [0, 4, 8, 12]
    with pytest.raises(ValueError, match="sync_period"):
        rig_contract.counter_sync_frames(4, 0)
