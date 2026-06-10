"""MCP server wiring — Streamable HTTP transport only (no stdio).

Adapts the SDK-agnostic tool specs in :mod:`skippy_mcp.mcp.tools` onto the MCP
low-level :class:`Server`, mounts it via ``StreamableHTTPSessionManager`` into a
Starlette ASGI app, and serves it with uvicorn. Optional Bearer-key auth and
TLS are driven by :class:`ServerConfig`.

The SDK validates tool input against each spec's ``inputSchema`` and converts
handler exceptions to ``isError`` results, so a raised :class:`SkippyError`
surfaces its actionable message. The blocking driver runs in a worker thread so
the event loop is never stalled.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
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

from skippy_mcp.config import ServerConfig
from skippy_mcp.core.errors import SkippyError
from skippy_mcp.driver.scope import Scope as ScopeDriver
from skippy_mcp.driver.session import establish, open_transport
from skippy_mcp.mcp.tools import ToolOutput, ToolSpec, build_tool_specs

logger = logging.getLogger("skippy_mcp")

MCP_PATH = "/mcp"
TIMEOUT_HEADER = "x-skippy-timeout-ms"

#: Test/fallback hook for the per-request VISA timeout override (ms). In the live
#: HTTP path the override is read from the request headers via the SDK request
#: context (see :func:`_resolve_timeout_override`); this ContextVar is consulted
#: only when no HTTP request context is available (direct handler calls, tests).
_request_timeout_ms: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "skippy_request_timeout_ms", default=None
)


def convert_output(output: ToolOutput) -> Any:
    """Map a :class:`ToolOutput` onto the SDK's accepted return shapes."""
    if output.image is not None:
        image = types.ImageContent(
            type="image",
            data=base64.b64encode(output.image.data).decode("ascii"),
            mimeType=f"image/{output.image.image_format.value}",
        )
        if output.structured is not None:
            return [image], output.structured
        return [image]
    return output.structured if output.structured is not None else {}


def build_mcp_server(
    scope: ScopeDriver, specs: list[ToolSpec], *, default_timeout_ms: int | None = None
) -> Server:
    """Build the low-level MCP server exposing ``specs`` backed by ``scope``.

    All tool calls are serialized through a single lock: one instrument session is
    not safe for concurrent write/query, so overlapping calls must not interleave.
    Each call applies its effective per-I/O timeout (the ``X-Skippy-Timeout-Ms``
    header override, else ``default_timeout_ms``) for the duration of the call.
    """
    server: Server = Server("skippy-mcp")
    by_name = {spec.name: spec for spec in specs}
    instrument_lock = anyio.Lock()

    def resolve_timeout_override() -> int | None:
        """Per-call timeout (ms) from the request header, else the ContextVar hook.

        The SDK sets ``request_context`` (with the Starlette request) in the same
        task that runs this handler, so the header is readable here even though
        the ASGI request task is separate under ``stateless=False``.
        """
        try:
            request = server.request_context.request
        except LookupError:
            request = None
        if request is not None and hasattr(request, "headers"):
            raw = request.headers.get(TIMEOUT_HEADER)
            if raw is not None:
                return _parse_timeout_header(raw)
        return _request_timeout_ms.get()

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
            raise SkippyError(
                "call_tool",
                reason=f"unknown tool {name!r}",
                check=f"use one of: {', '.join(by_name)}",
            )
        override = resolve_timeout_override()
        effective_ms = override if override is not None else default_timeout_ms
        try:
            # Serialize: the single instrument session is not concurrency-safe.
            async with instrument_lock:
                # The driver is blocking; never run it on the event loop.
                with scope.io_timeout(effective_ms):
                    output = await anyio.to_thread.run_sync(spec.handler, scope, arguments)
        except SkippyError:
            raise
        except Exception as exc:
            logger.exception("tool %s failed unexpectedly", name)
            raise SkippyError(
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


def _parse_timeout_header(raw: str) -> int | None:
    """Parse the ``X-Skippy-Timeout-Ms`` value; return None if absent/invalid."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("ignoring invalid %s header %r (not an integer)", TIMEOUT_HEADER, raw)
        return None
    if value < 0:
        logger.warning("ignoring invalid %s header %r (must be >= 0)", TIMEOUT_HEADER, raw)
        return None
    return value


def _security_settings(config: ServerConfig) -> TransportSecuritySettings:
    """Allowed Host/Origin values for DNS-rebinding protection (always enabled).

    Localhost (any port) and the concrete bind IP are accepted by default; other
    names a client may use (e.g. a LAN hostname, or anything when bound to
    0.0.0.0) must be added via ``allowed_hosts`` in the config.
    """
    hosts = {"localhost:*", "127.0.0.1:*", "[::1]:*"}
    if config.bind not in ("0.0.0.0", "::", "127.0.0.1", "localhost", "::1"):
        hosts.add(f"{config.bind}:*")
    hosts.update(config.allowed_hosts)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(hosts),
        allowed_origins=list(config.allowed_origins),
    )


def build_app(server: Server, config: ServerConfig, scope: ScopeDriver | None = None) -> Any:
    """Mount ``server`` as a Streamable HTTP ASGI app with DNS-rebinding
    protection and optional Bearer auth.

    The per-request ``X-Skippy-Timeout-Ms`` override is read inside the tool
    handler from the SDK request context, not here (see ``build_mcp_server``).
    If ``scope`` is given it is closed on shutdown so the instrument link (e.g. a
    VXI-11 link) is released rather than leaked across restarts.
    """
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
            if scope is not None:
                scope.close()

    app: Any = Starlette(routes=[Mount(MCP_PATH, app=handle_mcp)], lifespan=lifespan)
    if config.api_key is not None:
        app = BearerAuthMiddleware(app, config.api_key)
    return app


def startup_warnings(config: ServerConfig) -> list[str]:
    """Security warnings to surface at startup (empty when the posture is safe)."""
    warnings: list[str] = []
    if not config.is_loopback_bind and config.api_key is None:
        warnings.append(
            f"binding {config.bind}:{config.port} with NO authentication — anyone who can "
            "reach this port can control the instrument. Set 'api_key' in --config, or bind "
            "127.0.0.1."
        )
    if config.api_key is not None and not config.tls_enabled:
        warnings.append(
            "API key is sent in cleartext over plain HTTP — it can be sniffed on the network. "
            "Enable TLS (config 'tls') for confidentiality."
        )
    return warnings


def build_banner(config: ServerConfig, model: str, series: str, n_tools: int) -> str:
    """Human-readable startup banner describing the active mode + a smoke test."""
    from skippy_mcp import __version__

    endpoint = f"{config.scheme}://{config.bind}:{config.port}{MCP_PATH}"
    smoke_host = f"{config.scheme}://<host>:{config.port}{MCP_PATH}"
    auth_line = "    -H 'Authorization: Bearer <your-api-key>' \\\n" if config.api_key else ""
    timeout = "infinite" if config.timeout_ms == 0 else f"{config.timeout_ms} ms"
    init = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        '{"protocolVersion":"2024-11-05","capabilities":{},'
        '"clientInfo":{"name":"smoke","version":"1.0"}}}'
    )
    warn_block = ""
    for warning in startup_warnings(config):
        warn_block += f"  WARNING  : {warning}\n"
    return (
        f"SkippyMCP {__version__} — serving {model} ({series})\n"
        f"  Endpoint : {endpoint}\n"
        f"  TLS      : {'enabled' if config.tls_enabled else 'disabled'}\n"
        f"  API key  : {'enabled' if config.api_key else 'disabled'}\n"
        f"  Timeout  : {timeout} per I/O\n"
        f"  Tools    : {n_tools}\n"
        f"{warn_block}"
        f"  Smoke test:\n"
        f"    curl -sS {smoke_host} \\\n"
        f"    -H 'Content-Type: application/json' \\\n"
        f"    -H 'Accept: application/json, text/event-stream' \\\n"
        f"{auth_line}"
        f"    -d '{init}'"
    )


def main() -> None:
    """Console-script entry point: connect, then serve MCP over HTTP."""
    logging.basicConfig(level=logging.INFO)
    config = parse_args_or_exit()
    transport = open_transport(config)
    scope = establish(
        transport,
        reset_on_connect=config.reset_on_connect,
        default_timeout_ms=config.timeout_ms,
    )
    specs = build_tool_specs(config.allow_raw_scpi)
    server = build_mcp_server(scope, specs, default_timeout_ms=config.timeout_ms)
    app = build_app(server, config, scope=scope)

    idn = scope.identify()
    for warning in startup_warnings(config):
        logger.warning("%s", warning)
    print(build_banner(config, idn.model, scope.dialect_series, len(specs)), flush=True)

    uvicorn.run(
        app,
        host=config.bind,
        port=config.port,
        ssl_certfile=config.tls_cert,
        ssl_keyfile=config.tls_key,
        log_level="info",
    )


def parse_args_or_exit() -> ServerConfig:
    """Parse CLI/config, turning ConfigError into a clean stderr message + exit."""
    import sys

    from skippy_mcp.config import parse_args

    try:
        return parse_args()
    except SkippyError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
