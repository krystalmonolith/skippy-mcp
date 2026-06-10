"""H2 serialization + per-call timeout (header override) end-to-end.

These exercise the real SDK request handler and the full ASGI stack against
SimulatedTransport, so they verify both that tool calls are serialized and that
the ``X-Skippy-Timeout-Ms`` header propagates through to the transport.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from mcp import types
from starlette.testclient import TestClient

from skippy_mcp.config import ServerConfig
from skippy_mcp.driver.session import establish
from skippy_mcp.mcp.server import build_app, build_mcp_server
from skippy_mcp.mcp.tools import build_tool_specs
from skippy_mcp.transport.simulated import SimulatedTransport

_POST_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
_INIT = (
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
    '{"protocolVersion":"2024-11-05","capabilities":{},'
    '"clientInfo":{"name":"t","version":"1"}}}'
)
_INITIALIZED = '{"jsonrpc":"2.0","method":"notifications/initialized"}'


def _cfg(**kw: Any) -> ServerConfig:
    return ServerConfig(resource="TCPIP0::x::INSTR", **kw)


# -- per-call timeout override (ContextVar propagation) -------------------
def test_call_tool_applies_contextvar_timeout_override() -> None:
    """call_tool reads the per-request override and applies it via io_timeout."""
    from skippy_mcp.mcp.server import _request_timeout_ms

    transport = SimulatedTransport()
    scope = establish(transport, reset_on_connect=False, default_timeout_ms=300000)
    server = build_mcp_server(scope, build_tool_specs(False), default_timeout_ms=300000)
    handler = server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name="get_identity", arguments={}),
    )

    async def run() -> None:
        _request_timeout_ms.set(4242)
        await handler(req)

    asyncio.run(run())
    # Applied for the call (4242), then restored to the default (300000).
    assert 4242 in transport.timeout_history
    assert transport.timeout_history[-1] == 300000


def test_timeout_header_propagates_through_full_http_stack() -> None:
    """The X-Skippy-Timeout-Ms header reaches the transport across the ASGI stack."""
    transport = SimulatedTransport()
    cfg = _cfg()
    scope = establish(transport, reset_on_connect=False, default_timeout_ms=cfg.timeout_ms)
    server = build_mcp_server(scope, build_tool_specs(False), default_timeout_ms=cfg.timeout_ms)
    app = build_app(server, cfg)

    with TestClient(app, base_url="http://127.0.0.1:8080") as c:
        r = c.post("/mcp", content=_INIT, headers=_POST_HEADERS)
        assert r.status_code == 200
        sid = r.headers["mcp-session-id"]
        sess = {**_POST_HEADERS, "Mcp-Session-Id": sid}
        c.post("/mcp", content=_INITIALIZED, headers=sess)
        call = (
            '{"jsonrpc":"2.0","id":2,"method":"tools/call",'
            '"params":{"name":"get_identity","arguments":{}}}'
        )
        c.post("/mcp", content=call, headers={**sess, "X-Skippy-Timeout-Ms": "4242"})

    assert 4242 in transport.timeout_history, (
        "header did not propagate into call_tool — ContextVar fallback needed"
    )


def test_invalid_timeout_header_is_ignored() -> None:
    transport = SimulatedTransport()
    cfg = _cfg()
    scope = establish(transport, reset_on_connect=False, default_timeout_ms=cfg.timeout_ms)
    server = build_mcp_server(scope, build_tool_specs(False), default_timeout_ms=cfg.timeout_ms)
    app = build_app(server, cfg)

    with TestClient(app, base_url="http://127.0.0.1:8080") as c:
        r = c.post("/mcp", content=_INIT, headers=_POST_HEADERS)
        sid = r.headers["mcp-session-id"]
        sess = {**_POST_HEADERS, "Mcp-Session-Id": sid}
        c.post("/mcp", content=_INITIALIZED, headers=sess)
        call = (
            '{"jsonrpc":"2.0","id":2,"method":"tools/call",'
            '"params":{"name":"get_identity","arguments":{}}}'
        )
        c.post("/mcp", content=call, headers={**sess, "X-Skippy-Timeout-Ms": "not-a-number"})

    # Falls back to the server default; the bogus value never reaches the transport.
    assert transport.timeout_history  # io_timeout still ran
    assert all(v != "not-a-number" for v in transport.timeout_history)
    assert cfg.timeout_ms in transport.timeout_history


# -- H2 serialization -----------------------------------------------------
class _ConcurrencyProbe(SimulatedTransport):
    """Records the peak number of threads inside ``query`` at once."""

    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()

    def query(self, command: str) -> str:
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.02)  # widen the window so a missing lock would interleave
        with self._guard:
            self.active -= 1
        return super().query(command)


def test_tool_calls_are_serialized() -> None:
    transport = _ConcurrencyProbe()
    scope = establish(transport, reset_on_connect=False)
    server = build_mcp_server(scope, build_tool_specs(False))
    handler = server.request_handlers[types.CallToolRequest]

    def _req() -> types.CallToolRequest:
        return types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="measure", arguments={"type": "vpp", "source": "CH1"}
            ),
        )

    async def run() -> None:
        await asyncio.gather(handler(_req()), handler(_req()), handler(_req()))

    asyncio.run(run())
    assert transport.max_active == 1  # the instrument lock prevents overlap
