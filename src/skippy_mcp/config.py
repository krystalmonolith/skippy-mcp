"""Server configuration and command-line parsing.

SkippyMCP serves MCP over HTTP (Streamable HTTP). An optional JSON config file
(``--config``) may supply the instrument address, an API key, and TLS material;
CLI flags for the address always take precedence over the file.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skippy_mcp.core.errors import ConfigError

_CONFIG_HELP = """\
Optional JSON config file (--config). All keys are optional:

  {
    "host": "192.168.1.50",                         # instrument IP/hostname
    "resource": "TCPIP0::192.168.1.50::5555::SOCKET",  # full VISA resource
    "api_key": "your-bearer-token",                 # require Bearer auth if set
    "tls": { "cert": "/path/cert.pem", "key": "/path/key.pem" },  # serve HTTPS if set
    "allowed_hosts": ["scope-host.lan:*"],          # extra Host values to accept
    "allowed_origins": ["https://app.example.com"]  # extra Origin values to accept
  }

Address precedence (highest first): --resource, --host, JSON resource, JSON host.
No api_key -> no auth.  No tls -> plain HTTP.  No config file -> plain HTTP, no auth.

DNS-rebinding protection is always on: requests are accepted only if their Host
header matches the bind address or localhost (any port). Add other names a client
may use (e.g. a LAN hostname) via "allowed_hosts"; add browser Origins via
"allowed_origins". The --timeout-ms value (default 300000) is the per-I/O VISA
timeout; 0 means wait forever.
"""

_ALLOWED_KEYS = {"host", "resource", "api_key", "tls", "allowed_hosts", "allowed_origins"}
_ALLOWED_TLS_KEYS = {"cert", "key"}


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Resolved runtime configuration for the HTTP MCP server."""

    resource: str
    bind: str = "127.0.0.1"
    port: int = 8080
    timeout_ms: int = 300000
    reset_on_connect: bool = True
    allow_raw_scpi: bool = False
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
        """True if the bind address is a loopback/localhost address (not exposed)."""
        return self.bind in ("127.0.0.1", "localhost", "::1")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skippy-mcp",
        description="MCP server (HTTP transport) for controlling Rigol oscilloscopes via SCPI.",
        epilog=_CONFIG_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--host", help="Instrument IP/hostname (overrides JSON host/resource).")
    target.add_argument("--resource", help="Full VISA resource string (highest precedence).")
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="HTTP bind address (default 127.0.0.1; use 0.0.0.0 to expose on the LAN).",
    )
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default 8080).")
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=300000,
        help="Per-I/O VISA timeout in ms (default 300000; 0 = wait forever).",
    )
    parser.add_argument(
        "--no-reset",
        dest="reset_on_connect",
        action="store_false",
        help="Do not issue *RST on connect; leave the scope's setup untouched.",
    )
    parser.add_argument(
        "--allow-raw-scpi",
        action="store_true",
        help="Register the scpi_raw escape-hatch tool.",
    )
    parser.add_argument("--config", help="Path to an optional JSON config file (see below).")
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
    unknown = set(data) - _ALLOWED_KEYS
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
    """Validate an optional config key as a list of non-empty strings."""
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


def parse_args(argv: Sequence[str] | None = None) -> ServerConfig:
    """Parse argv (+ optional JSON config) into a :class:`ServerConfig`."""
    ns = _build_parser().parse_args(argv)
    data: dict[str, Any] = _load_config_file(ns.config) if ns.config else {}

    # Address precedence: --resource, --host, JSON resource, JSON host.
    if ns.resource:
        resource = str(ns.resource)
    elif ns.host:
        resource = f"TCPIP0::{ns.host}::INSTR"
    elif data.get("resource"):
        resource = str(data["resource"])
    elif data.get("host"):
        resource = f"TCPIP0::{data['host']}::INSTR"
    else:
        raise ConfigError(
            "configure",
            reason="no instrument address supplied",
            check="pass --host/--resource, or set host/resource in the --config file",
        )

    if ns.timeout_ms < 0:
        raise ConfigError(
            "configure",
            reason="timeout must be 0 (wait forever) or a positive number of ms",
            check="pass --timeout-ms with a value >= 0",
            inputs={"timeout_ms": ns.timeout_ms},
        )

    api_key = data.get("api_key")
    if api_key is not None and not (isinstance(api_key, str) and api_key):
        raise ConfigError(
            "configure",
            reason="'api_key' must be a non-empty string",
            check="set api_key to a non-empty token, or omit it for no auth",
        )
    tls_cert, tls_key = _resolve_tls(data)
    allowed_hosts = _resolve_str_list(data, "allowed_hosts")
    allowed_origins = _resolve_str_list(data, "allowed_origins")

    return ServerConfig(
        resource=resource,
        bind=str(ns.bind),
        port=int(ns.port),
        timeout_ms=int(ns.timeout_ms),
        reset_on_connect=bool(ns.reset_on_connect),
        allow_raw_scpi=bool(ns.allow_raw_scpi),
        api_key=api_key,
        tls_cert=tls_cert,
        tls_key=tls_key,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
