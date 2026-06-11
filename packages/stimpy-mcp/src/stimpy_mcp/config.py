"""Server configuration and command-line parsing.

StimpyMCP serves MCP over HTTP (Streamable HTTP) and drives GPIO via a local
pigpio daemon. An optional JSON config file (``--config``) supplies the API key,
TLS material, pin-map override, and pigpio coordinates; CLI flags win over it.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rig_contract

from stimpy_mcp.core.errors import ConfigError

_CONFIG_HELP = """\
JSON config file (--config). REQUIRED unless --simulate. "pin_map" is REQUIRED
(the BCM wiring is deployment-specific); all other keys are optional:

  {
    "pin_map": { "data": [4,5,...,26], "sync": 27 }, # REQUIRED: BCM pins D0..D15 + SYNC
    "api_key": "your-bearer-token",                 # require Bearer auth if set
    "tls": { "cert": "/path/cert.pem", "key": "/path/key.pem" },  # serve HTTPS if set
    "pigpio_host": "localhost",                      # pigpiod address (default localhost)
    "pigpio_port": 8888,                             # pigpiod port (default 8888)
    "default_clock_rate_hz": 1000.0,                 # frame clock if a pattern omits one
    "allow_builtin_patterns": false,                 # register the gated load_counter tool
    "allowed_hosts": ["rigelpi:*"],                  # extra Host values to accept
    "allowed_origins": ["https://app.example.com"]   # extra Origin values to accept
  }

No api_key -> no auth. No tls -> plain HTTP. DNS-rebinding protection is always on.
With --simulate (no real GPIO), --config may be omitted and a reference pin map is used.
"""

_ALLOWED_KEYS = {
    "api_key", "tls", "pigpio_host", "pigpio_port", "default_clock_rate_hz",
    "allow_builtin_patterns", "pin_map", "allowed_hosts", "allowed_origins",
}
_ALLOWED_TLS_KEYS = {"cert", "key"}
_ALLOWED_PINMAP_KEYS = {"data", "sync"}
_MAX_BCM = 27  # Pi 40-pin header GPIO range is BCM 0..27


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Resolved runtime configuration for the HTTP MCP server."""

    bind: str = "127.0.0.1"
    port: int = 8080
    pigpio_host: str = "localhost"
    pigpio_port: int = 8888
    simulate: bool = False
    default_clock_rate_hz: float = 1000.0
    data_pins: tuple[int, ...] = rig_contract.DATA_PINS
    sync_pin: int = rig_contract.SYNC_PIN
    allow_builtin_patterns: bool = False
    api_key: str | None = None
    tls_cert: str | None = None
    tls_key: str | None = None
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()

    @property
    def tls_enabled(self) -> bool:
        return self.tls_cert is not None and self.tls_key is not None

    @property
    def scheme(self) -> str:
        return "https" if self.tls_enabled else "http"

    @property
    def is_loopback_bind(self) -> bool:
        return self.bind in ("127.0.0.1", "localhost", "::1")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stimpy-mcp",
        description="MCP server (HTTP) driving a Raspberry Pi GPIO digital-stimulus generator.",
        epilog=_CONFIG_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--bind", default="127.0.0.1",
        help="HTTP bind address (default 127.0.0.1; use the Pi's LAN IP to expose).",
    )
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default 8080).")
    parser.add_argument(
        "--pigpio-host", default="localhost", help="pigpiod host (default localhost)."
    )
    parser.add_argument(
        "--pigpio-port", type=int, default=8888, help="pigpiod port (default 8888)."
    )
    parser.add_argument(
        "--simulate", action="store_true",
        help="Use the in-memory simulator instead of real GPIO (dev/CI; drives nothing).",
    )
    parser.add_argument(
        "--clock-rate", type=float, dest="clock_rate",
        help="Default frame clock in Hz (default 1000).",
    )
    parser.add_argument(
        "--allow-builtin-patterns", action="store_true",
        help="Register the gated load_counter convenience tool.",
    )
    parser.add_argument(
        "--config", help="Path to the JSON config file (required unless --simulate; see below)."
    )
    return parser


def _load_config_file(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    if not path.is_file():
        raise ConfigError(
            "configure",
            reason=f"config file {path_str!r} does not exist",
            check="pass --config with a path to a readable JSON file",
            inputs={"config": path_str},
        )
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            "configure",
            reason=f"config file {path_str!r} is not valid JSON: {exc}",
            check="fix the JSON syntax (see --help for the expected format)",
            inputs={"config": path_str},
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError(
            "configure",
            reason="config file must contain a JSON object",
            check="see --help for the expected format",
            inputs={"config": path_str},
        )
    # Keys starting with "_" are treated as comments (JSON has none) and ignored.
    unknown = {k for k in data if not k.startswith("_")} - _ALLOWED_KEYS
    if unknown:
        raise ConfigError(
            "configure",
            reason=f"unknown config key(s): {', '.join(sorted(unknown))}",
            check=f"allowed keys are: {', '.join(sorted(_ALLOWED_KEYS))}",
        )
    return data


def _resolve_tls(data: dict[str, Any]) -> tuple[str | None, str | None]:
    tls = data.get("tls")
    if tls is None:
        return None, None
    if not isinstance(tls, dict) or set(tls) - _ALLOWED_TLS_KEYS or not {"cert", "key"} <= set(tls):
        raise ConfigError(
            "configure",
            reason="'tls' must be an object with both 'cert' and 'key'",
            check="provide tls.cert and tls.key paths, or omit 'tls' for plain HTTP",
        )
    for label, value in (("cert", tls["cert"]), ("key", tls["key"])):
        if not Path(str(value)).is_file():
            raise ConfigError(
                "configure",
                reason=f"TLS {label} file {value!r} does not exist or is unreadable",
                check=f"point tls.{label} at a readable PEM file",
                inputs={f"tls.{label}": value},
            )
    return str(tls["cert"]), str(tls["key"])


def _resolve_str_list(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(
            "configure",
            reason=f"{key!r} must be a list of non-empty strings",
            check=f"set {key} to a JSON array of host/origin strings, or omit it",
        )
    return tuple(value)


def _resolve_pin_map(data: dict[str, Any]) -> tuple[tuple[int, ...], int]:
    pm = data.get("pin_map")
    if pm is None:
        return rig_contract.DATA_PINS, rig_contract.SYNC_PIN
    if (
        not isinstance(pm, dict)
        or set(pm) - _ALLOWED_PINMAP_KEYS
        or not {"data", "sync"} <= set(pm)
    ):
        raise ConfigError(
            "configure",
            reason="'pin_map' must be an object with 'data' (list) and 'sync' (int)",
            check="e.g. {\"data\": [4,5,...,26], \"sync\": 27}, or omit for the default",
        )
    data_pins = pm["data"]
    sync = pm["sync"]

    def _is_bcm(v: object) -> bool:
        return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= _MAX_BCM

    if (
        not isinstance(data_pins, list)
        or len(data_pins) != rig_contract.CHANNEL_COUNT
        or not all(_is_bcm(v) for v in data_pins)
    ):
        raise ConfigError(
            "configure",
            reason=f"pin_map.data must be {rig_contract.CHANNEL_COUNT} BCM ints in 0..{_MAX_BCM}",
            check="list exactly 16 distinct BCM pin numbers for D0..D15",
        )
    if not _is_bcm(sync):
        raise ConfigError(
            "configure",
            reason=f"pin_map.sync must be a BCM int in 0..{_MAX_BCM}",
            check="set sync to a BCM pin not used by the data lines",
        )
    all_pins = [*data_pins, sync]
    if len(set(all_pins)) != len(all_pins):
        raise ConfigError(
            "configure",
            reason="pin_map pins must all be distinct (data lines + sync)",
            check="remove the duplicate BCM number",
        )
    return tuple(int(v) for v in data_pins), int(sync)


def parse_args(argv: Sequence[str] | None = None) -> ServerConfig:
    """Parse argv (+ JSON config) into a :class:`ServerConfig` (CLI wins).

    ``--config`` is required (it carries the deployment-specific ``pin_map``)
    except under ``--simulate``, which drives no real GPIO and falls back to the
    reference pin map.
    """
    ns = _build_parser().parse_args(argv)
    if not ns.config and not ns.simulate:
        raise ConfigError(
            "configure",
            reason="no --config supplied",
            check=(
                "pass --config with a JSON file containing 'pin_map' (and api_key/tls for a "
                "non-loopback bind); or use --simulate for a dry run with no GPIO"
            ),
        )
    data: dict[str, Any] = _load_config_file(ns.config) if ns.config else {}
    if ns.config and "pin_map" not in data:
        raise ConfigError(
            "configure",
            reason="config is missing the required 'pin_map'",
            check='add "pin_map": {"data": [16 BCM ints for D0..D15], "sync": <BCM int>}',
        )

    api_key = data.get("api_key")
    if api_key is not None and not (isinstance(api_key, str) and api_key):
        raise ConfigError(
            "configure",
            reason="'api_key' must be a non-empty string",
            check="set api_key to a non-empty token, or omit it for no auth",
        )

    clock = (
        ns.clock_rate if ns.clock_rate is not None else data.get("default_clock_rate_hz", 1000.0)
    )
    if not isinstance(clock, (int, float)) or isinstance(clock, bool) or clock <= 0:
        raise ConfigError(
            "configure",
            reason="default clock rate must be a positive number of Hz",
            check="pass --clock-rate > 0 or set default_clock_rate_hz",
            inputs={"clock_rate": clock},
        )

    pigpio_host = (
        data.get("pigpio_host", "localhost") if ns.pigpio_host == "localhost" else ns.pigpio_host
    )
    pigpio_port = data.get("pigpio_port", 8888) if ns.pigpio_port == 8888 else ns.pigpio_port
    if (
        not isinstance(pigpio_port, int)
        or isinstance(pigpio_port, bool)
        or not 1 <= pigpio_port <= 65535
    ):
        raise ConfigError(
            "configure",
            reason="pigpio_port must be an integer in 1..65535",
            check="set pigpio_port to the pigpiod port (default 8888)",
            inputs={"pigpio_port": pigpio_port},
        )

    data_pins, sync_pin = _resolve_pin_map(data)
    tls_cert, tls_key = _resolve_tls(data)
    allow_builtin = bool(ns.allow_builtin_patterns or data.get("allow_builtin_patterns", False))

    return ServerConfig(
        bind=str(ns.bind),
        port=int(ns.port),
        pigpio_host=str(pigpio_host),
        pigpio_port=int(pigpio_port),
        simulate=bool(ns.simulate),
        default_clock_rate_hz=float(clock),
        data_pins=data_pins,
        sync_pin=sync_pin,
        allow_builtin_patterns=allow_builtin,
        api_key=api_key,
        tls_cert=tls_cert,
        tls_key=tls_key,
        allowed_hosts=_resolve_str_list(data, "allowed_hosts"),
        allowed_origins=_resolve_str_list(data, "allowed_origins"),
    )
