"""MCP server wiring — Streamable HTTP transport only (no stdio).

Adapts the SDK-agnostic tool specs in :mod:`stimpy_mcp.mcp.tools` onto the MCP
low-level :class:`Server`, mounts it via ``StreamableHTTPSessionManager`` into a
Starlette ASGI app, and serves it with uvicorn. Optional Bearer-key auth and TLS
are driven by :class:`ServerConfig`. The blocking driver runs in a worker thread,
serialized by a lock (the single engine is not concurrency-safe).
"""

from __future__ import annotations

import contextlib
import hmac
import logging
from collections.abc import AsyncIterator
from typing import Any

import anyio
import uvicorn
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send

from stimpy_mcp.config import ServerConfig
from stimpy_mcp.core.errors import StimpyError
from stimpy_mcp.driver.stimulus import StimulusDriver
from stimpy_mcp.mcp.tools import ToolOutput, ToolSpec, build_tool_specs

logger = logging.getLogger("stimpy_mcp")

MCP_PATH = "/mcp"


def convert_output(output: ToolOutput) -> Any:
    """Map a :class:`ToolOutput` onto the SDK's accepted return shapes."""
    return output.structured if output.structured is not None else {}


def build_mcp_server(driver: StimulusDriver, specs: list[ToolSpec]) -> Server:
    """Build the low-level MCP server exposing ``specs`` backed by ``driver``.

    All tool calls are serialized through a single lock: one engine session is
    not safe for concurrent staging/swapping.
    """
    server: Server = Server("stimpy-mcp")
    by_name = {spec.name: spec for spec in specs}
    engine_lock = anyio.Lock()

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=s.name, description=s.description, inputSchema=s.input_schema)
            for s in specs
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
        spec = by_name.get(name)
        if spec is None:
            raise StimpyError(
                "call_tool",
                reason=f"unknown tool {name!r}",
                check=f"use one of: {', '.join(by_name)}",
            )
        try:
            async with engine_lock:  # the single engine is not concurrency-safe
                output = await anyio.to_thread.run_sync(spec.handler, driver, arguments)
        except StimpyError:
            raise
        except Exception as exc:
            logger.exception("tool %s failed unexpectedly", name)
            raise StimpyError(
                name,
                reason="an unexpected internal error occurred",
                check="check the server logs for a traceback",
            ) from exc
        return convert_output(output)

    return server


class BearerAuthMiddleware:
    """Pure-ASGI middleware: require ``Authorization: Bearer <key>`` on HTTP requests.

    Implemented at the ASGI layer (not BaseHTTPMiddleware) so it does not buffer
    the transport's streaming/SSE responses.
    """

    def __init__(self, app: Any, api_key: str) -> None:
        self._app = app
        self._expected = f"Bearer {api_key}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            presented = headers.get(b"authorization", b"").decode("latin-1")
            if not hmac.compare_digest(presented, self._expected):
                response = JSONResponse(
                    {"error": "unauthorized", "detail": "missing or invalid Bearer API key"},
                    status_code=401,
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


def _security_settings(config: ServerConfig) -> TransportSecuritySettings:
    """Allowed Host/Origin values for DNS-rebinding protection (always enabled)."""
    hosts = {"localhost:*", "127.0.0.1:*", "[::1]:*"}
    if config.bind not in ("0.0.0.0", "::", "127.0.0.1", "localhost", "::1"):
        hosts.add(f"{config.bind}:*")
    hosts.update(config.allowed_hosts)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(hosts),
        allowed_origins=list(config.allowed_origins),
    )


def build_app(server: Server, config: ServerConfig, driver: StimulusDriver | None = None) -> Any:
    """Mount ``server`` as a Streamable HTTP ASGI app with DNS-rebinding protection
    and optional Bearer auth. If ``driver`` is given it is closed on shutdown."""
    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=False,
        stateless=False,
        security_settings=_security_settings(config),
    )

    async def handle_mcp(asgi_scope: Scope, receive: Receive, send: Send) -> None:
        await manager.handle_request(asgi_scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        try:
            async with manager.run():
                yield
        finally:
            if driver is not None:
                driver.close()

    app: Any = Starlette(routes=[Mount(MCP_PATH, app=handle_mcp)], lifespan=lifespan)
    if config.api_key is not None:
        app = BearerAuthMiddleware(app, config.api_key)
    return app


def startup_warnings(config: ServerConfig) -> list[str]:
    """Security/operational warnings to surface at startup (empty when safe)."""
    warnings: list[str] = []
    if not config.is_loopback_bind and config.api_key is None:
        warnings.append(
            f"binding {config.bind}:{config.port} with NO authentication — anyone who can "
            "reach this port can drive the GPIO. Set 'api_key' in --config, or bind 127.0.0.1."
        )
    if config.api_key is not None and not config.tls_enabled:
        warnings.append(
            "API key is sent in cleartext over plain HTTP — it can be sniffed on the network. "
            "Enable TLS (config 'tls') for confidentiality."
        )
    if config.simulate:
        warnings.append("running in --simulate mode: NO real GPIO is driven (dev/CI only).")
    return warnings


def build_banner(config: ServerConfig, n_tools: int) -> str:
    """Human-readable startup banner describing the active mode + a smoke test."""
    from stimpy_mcp import __version__

    endpoint = f"{config.scheme}://{config.bind}:{config.port}{MCP_PATH}"
    smoke_host = f"{config.scheme}://<host>:{config.port}{MCP_PATH}"
    auth_line = "    -H 'Authorization: Bearer <your-api-key>' \\\n" if config.api_key else ""
    backend = (
        "SIMULATED" if config.simulate
        else f"lgpio @ /dev/gpiochip{config.gpiochip}"
    )
    init = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        '{"protocolVersion":"2024-11-05","capabilities":{},'
        '"clientInfo":{"name":"smoke","version":"1.0"}}}'
    )
    warn_block = ""
    for warning in startup_warnings(config):
        warn_block += f"  WARNING  : {warning}\n"
    return (
        f"StimpyMCP {__version__} — GPIO digital-stimulus generator\n"
        f"  Endpoint : {endpoint}\n"
        f"  Backend  : {backend}\n"
        f"  Clock    : {config.default_clock_rate_hz:g} Hz default\n"
        f"  TLS      : {'enabled' if config.tls_enabled else 'disabled'}\n"
        f"  API key  : {'enabled' if config.api_key else 'disabled'}\n"
        f"  Tools    : {n_tools}\n"
        f"{warn_block}"
        f"  Smoke test:\n"
        f"    curl -sS {smoke_host} \\\n"
        f"    -H 'Content-Type: application/json' \\\n"
        f"    -H 'Accept: application/json, text/event-stream' \\\n"
        f"{auth_line}"
        f"    -d '{init}'"
    )


def parse_args_or_exit() -> ServerConfig:
    """Parse CLI/config, turning ConfigError into a clean stderr message + exit."""
    import sys

    from stimpy_mcp.config import parse_args

    try:
        return parse_args()
    except StimpyError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc


def main() -> None:
    """Console-script entry point: open the engine, then serve MCP over HTTP."""
    from stimpy_mcp.driver.session import establish, open_engine

    logging.basicConfig(level=logging.INFO)
    config = parse_args_or_exit()
    engine = open_engine(config)
    driver = establish(engine, config)
    specs = build_tool_specs(config.allow_builtin_patterns)
    server = build_mcp_server(driver, specs)
    app = build_app(server, config, driver=driver)

    for warning in startup_warnings(config):
        logger.warning("%s", warning)
    print(build_banner(config, len(specs)), flush=True)

    uvicorn.run(
        app,
        host=config.bind,
        port=config.port,
        ssl_certfile=config.tls_cert,
        ssl_keyfile=config.tls_key,
        log_level="info",
    )


if __name__ == "__main__":
    main()
