"""Server configuration and command-line parsing."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

from skippy_mcp.core.errors import ConfigError


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Resolved runtime configuration for the MCP server."""

    resource: str
    timeout_ms: int = 5000
    async_dispatch: bool = False
    reset_on_connect: bool = True
    allow_raw_scpi: bool = False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skippy-mcp",
        description="MCP server for controlling Rigol oscilloscopes via SCPI.",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--host", help="Instrument IP/hostname (TCPIP INSTR resource is built).")
    target.add_argument("--resource", help="Full VISA resource string (overrides --host).")
    parser.add_argument("--timeout-ms", type=int, default=5000, help="VISA I/O timeout (ms).")
    parser.add_argument(
        "--async",
        dest="async_dispatch",
        action="store_true",
        help="Dispatch tool calls via a thread executor.",
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
    return parser


def parse_args(argv: Sequence[str] | None = None) -> ServerConfig:
    """Parse argv into a :class:`ServerConfig`, raising :class:`ConfigError`."""
    ns = _build_parser().parse_args(argv)
    if ns.resource:
        resource = str(ns.resource)
    elif ns.host:
        resource = f"TCPIP0::{ns.host}::INSTR"
    else:
        raise ConfigError(
            "configure",
            reason="no instrument address supplied",
            check="pass --host <ip|hostname> or --resource <visa-resource-string>",
        )
    if ns.timeout_ms <= 0:
        raise ConfigError(
            "configure",
            reason="timeout must be positive",
            check="pass --timeout-ms with a value greater than 0",
            inputs={"timeout_ms": ns.timeout_ms},
        )
    return ServerConfig(
        resource=resource,
        timeout_ms=ns.timeout_ms,
        async_dispatch=ns.async_dispatch,
        reset_on_connect=ns.reset_on_connect,
        allow_raw_scpi=ns.allow_raw_scpi,
    )
