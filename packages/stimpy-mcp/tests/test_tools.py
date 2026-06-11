"""Tool handlers against the simulator (no MCP SDK, no event loop)."""

from __future__ import annotations

from typing import Any

import pytest
import rig_contract

from stimpy_mcp.core.errors import ValidationError
from stimpy_mcp.core.models import PinMap
from stimpy_mcp.driver.stimulus import StimulusDriver
from stimpy_mcp.engine.simulated import SimulatedEngine
from stimpy_mcp.mcp.tools import build_tool_specs

PIN_MAP = PinMap(data_pins=rig_contract.DATA_PINS, sync_pin=rig_contract.SYNC_PIN)


def _driver() -> StimulusDriver:
    e = SimulatedEngine()
    e.configure(PIN_MAP)
    return StimulusDriver(e, PIN_MAP, default_clock_rate_hz=1000.0)


def _call(specs: list, name: str, driver: StimulusDriver, args: dict[str, Any]) -> dict[str, Any]:
    spec = next(s for s in specs if s.name == name)
    out = spec.handler(driver, args)
    assert out.structured is not None
    return out.structured


def test_default_surface_has_no_gated_tool() -> None:
    names = {s.name for s in build_tool_specs(allow_builtin_patterns=False)}
    assert names == {"get_status", "get_pin_map", "set_pattern", "set_clock_rate", "start", "stop"}


def test_gated_load_counter_appears_when_enabled() -> None:
    specs = build_tool_specs(allow_builtin_patterns=True)
    names = {s.name for s in specs}
    assert "load_counter" in names
    assert next(s for s in specs if s.name == "load_counter").gated is True


def test_get_pin_map() -> None:
    specs = build_tool_specs()
    d = _driver()
    out = _call(specs, "get_pin_map", d, {})
    assert out["data_pins"] == list(rig_contract.DATA_PINS)
    assert out["sync_pin"] == rig_contract.SYNC_PIN
    assert out["channel_count"] == 16


def test_set_pattern_then_status() -> None:
    specs = build_tool_specs()
    d = _driver()
    out = _call(specs, "set_pattern", d, {"frames": [0, 1, 2, 3], "clock_rate_hz": 1000.0})
    assert out["status"] == "live"
    assert out["buffer_frames"] == 4
    st = _call(specs, "get_status", d, {})
    assert st["running"] is True


def test_set_pattern_staged_only() -> None:
    specs = build_tool_specs()
    d = _driver()
    out = _call(specs, "set_pattern", d, {"frames": [0, 1], "go_live": False})
    assert out["status"] == "staged"


def test_stop_tool() -> None:
    specs = build_tool_specs()
    d = _driver()
    _call(specs, "set_pattern", d, {"frames": [0, 1, 2, 3]})
    out = _call(specs, "stop", d, {})
    assert out["status"] == "stopped"
    assert out["running"] is False


def test_start_bad_mode_rejected() -> None:
    specs = build_tool_specs()
    d = _driver()
    _call(specs, "set_pattern", d, {"frames": [0, 1], "go_live": False})
    with pytest.raises(ValidationError):
        _call(specs, "start", d, {"mode": "forever"})


def test_load_counter_stages_counter() -> None:
    specs = build_tool_specs(allow_builtin_patterns=True)
    d = _driver()
    out = _call(specs, "load_counter", d, {"bits": 4, "sync_period": 4})
    assert out["buffer_frames"] == 16  # 2**4
    assert out["bits"] == 4
    assert out["running"] is True
