"""Config + CLI precedence and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import rig_contract

from stimpy_mcp.config import parse_args
from stimpy_mcp.core.errors import ConfigError

VALID_PINMAP = {"data": list(range(16)), "sync": 26}


def _cfg(tmp_path: Path, **extra: object) -> str:
    """Write a valid config (pin_map included) plus overrides; return its path."""
    data: dict[str, object] = {"pin_map": VALID_PINMAP, **extra}
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_config_required_without_simulate() -> None:
    with pytest.raises(ConfigError) as exc:
        parse_args([])
    assert "no --config supplied" in str(exc.value)


def test_simulate_allows_no_config() -> None:
    c = parse_args(["--simulate"])
    assert c.simulate is True
    assert c.data_pins == rig_contract.DATA_PINS  # reference map
    assert c.sync_pin == rig_contract.SYNC_PIN
    assert c.is_loopback_bind is True


def test_config_requires_pin_map(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"api_key": "x"}))  # no pin_map
    with pytest.raises(ConfigError) as exc:
        parse_args(["--config", str(p)])
    assert "pin_map" in str(exc.value)


def test_cli_flags() -> None:
    c = parse_args(["--bind", "0.0.0.0", "--port", "9000", "--simulate", "--clock-rate", "4096"])
    assert c.bind == "0.0.0.0"
    assert c.port == 9000
    assert c.simulate is True
    assert c.default_clock_rate_hz == 4096.0
    assert c.is_loopback_bind is False


def test_cli_clock_beats_json(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, default_clock_rate_hz=2000.0)
    c = parse_args(["--clock-rate", "5000", "--config", cfg])
    assert c.default_clock_rate_hz == 5000.0  # CLI wins


def test_json_supplies_values(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, api_key="secret", pigpio_port=9999)
    c = parse_args(["--config", cfg])
    assert c.api_key == "secret"
    assert c.pigpio_port == 9999


def test_comment_keys_ignored(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _comment="this is a note")
    c = parse_args(["--config", cfg])
    assert c.data_pins == tuple(VALID_PINMAP["data"])


def test_unknown_key_rejected(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, nope=1)
    with pytest.raises(ConfigError):
        parse_args(["--config", cfg])


def test_pin_map_override(tmp_path: Path) -> None:
    c = parse_args(["--config", _cfg(tmp_path)])
    assert c.data_pins == tuple(VALID_PINMAP["data"])
    assert c.sync_pin == 26


def test_pin_map_duplicate_rejected(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"pin_map": {"data": list(range(16)), "sync": 5}}))  # 5 dups
    with pytest.raises(ConfigError):
        parse_args(["--config", str(p)])


def test_pin_map_wrong_count_rejected(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"pin_map": {"data": [1, 2, 3], "sync": 26}}))
    with pytest.raises(ConfigError):
        parse_args(["--config", str(p)])


def test_bad_api_key_rejected(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, api_key="")
    with pytest.raises(ConfigError):
        parse_args(["--config", cfg])
