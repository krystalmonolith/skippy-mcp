"""Config + CLI parsing, including the optional JSON config file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skippy_mcp.config import parse_args
from skippy_mcp.core.errors import ConfigError


def _write(tmp_path: Path, obj: object) -> str:
    p = tmp_path / "skippy.json"
    p.write_text(json.dumps(obj))
    return str(p)


# -- address resolution / precedence -------------------------------------
def test_host_builds_tcpip_resource() -> None:
    cfg = parse_args(["--host", "192.168.1.50"])
    assert cfg.resource == "TCPIP0::192.168.1.50::INSTR"


def test_defaults() -> None:
    cfg = parse_args(["--host", "x"])
    assert cfg.bind == "127.0.0.1"
    assert cfg.is_loopback_bind is True
    assert cfg.port == 8080
    assert cfg.timeout_ms == 300000
    assert cfg.reset_on_connect is True
    assert cfg.api_key is None
    assert cfg.tls_enabled is False
    assert cfg.scheme == "http"
    assert cfg.allowed_hosts == ()
    assert cfg.allowed_origins == ()


# -- timeout --------------------------------------------------------------
def test_timeout_zero_allowed_means_infinite() -> None:
    cfg = parse_args(["--host", "x", "--timeout-ms", "0"])
    assert cfg.timeout_ms == 0


def test_negative_timeout_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_args(["--host", "x", "--timeout-ms", "-1"])


def test_nonloopback_bind_not_loopback() -> None:
    cfg = parse_args(["--host", "x", "--bind", "0.0.0.0"])
    assert cfg.is_loopback_bind is False


# -- allowed hosts / origins ---------------------------------------------
def test_allowed_hosts_and_origins_from_json(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        {"host": "x", "allowed_hosts": ["scope.lan:*"], "allowed_origins": ["https://app"]},
    )
    cfg = parse_args(["--config", cfg_path])
    assert cfg.allowed_hosts == ("scope.lan:*",)
    assert cfg.allowed_origins == ("https://app",)


def test_allowed_hosts_must_be_string_list(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path, {"host": "x", "allowed_hosts": [1, 2]})
    with pytest.raises(ConfigError):
        parse_args(["--config", cfg_path])


def test_no_address_anywhere_raises() -> None:
    with pytest.raises(ConfigError):
        parse_args([])


def test_json_supplies_address(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path, {"resource": "TCPIP0::scope::5555::SOCKET"})
    cfg = parse_args(["--config", cfg_path])
    assert cfg.resource == "TCPIP0::scope::5555::SOCKET"


def test_cli_resource_overrides_json(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path, {"host": "1.1.1.1", "resource": "TCPIP0::jsonres::INSTR"})
    cfg = parse_args(["--config", cfg_path, "--resource", "TCPIP0::cli::5555::SOCKET"])
    assert cfg.resource == "TCPIP0::cli::5555::SOCKET"


def test_cli_host_overrides_json_host(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path, {"host": "json-host"})
    cfg = parse_args(["--config", cfg_path, "--host", "cli-host"])
    assert cfg.resource == "TCPIP0::cli-host::INSTR"


# -- api key --------------------------------------------------------------
def test_api_key_from_json(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path, {"host": "x", "api_key": "s3cret"})
    cfg = parse_args(["--config", cfg_path])
    assert cfg.api_key == "s3cret"


def test_empty_api_key_rejected(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path, {"host": "x", "api_key": ""})
    with pytest.raises(ConfigError):
        parse_args(["--config", cfg_path])


# -- tls ------------------------------------------------------------------
def test_tls_from_json(tmp_path: Path) -> None:
    cert = tmp_path / "c.pem"
    key = tmp_path / "k.pem"
    cert.write_text("cert")
    key.write_text("key")
    cfg_path = _write(tmp_path, {"host": "x", "tls": {"cert": str(cert), "key": str(key)}})
    cfg = parse_args(["--config", cfg_path])
    assert cfg.tls_enabled is True
    assert cfg.scheme == "https"


def test_tls_missing_file_rejected(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path, {"host": "x", "tls": {"cert": "/nope/c.pem", "key": "/nope/k.pem"}})
    with pytest.raises(ConfigError):
        parse_args(["--config", cfg_path])


def test_tls_requires_both_cert_and_key(tmp_path: Path) -> None:
    cert = tmp_path / "c.pem"
    cert.write_text("cert")
    cfg_path = _write(tmp_path, {"host": "x", "tls": {"cert": str(cert)}})
    with pytest.raises(ConfigError):
        parse_args(["--config", cfg_path])


# -- file validation ------------------------------------------------------
def test_missing_config_file_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_args(["--config", "/nonexistent/skippy.json"])


def test_invalid_json_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(ConfigError):
        parse_args(["--config", str(p)])


def test_unknown_key_rejected(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path, {"host": "x", "bogus": 1})
    with pytest.raises(ConfigError):
        parse_args(["--config", cfg_path])
