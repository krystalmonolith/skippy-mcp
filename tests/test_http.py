"""HTTP transport: Bearer-auth gating and the startup banner.

The auth checks run against the real ASGI stack via Starlette's TestClient (which
also drives the transport's lifespan); we assert only the 401 gate, not a full
MCP handshake.
"""

from __future__ import annotations

from typing import Any

from starlette.testclient import TestClient

from skippy_mcp.config import ServerConfig
from skippy_mcp.driver.session import establish
from skippy_mcp.mcp.server import build_app, build_banner, build_mcp_server
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


def _app(api_key: str | None = None) -> Any:
    scope = establish(SimulatedTransport(), reset_on_connect=False)
    server = build_mcp_server(scope, build_tool_specs(allow_raw_scpi=False))
    return build_app(server, api_key=api_key)


def _cfg(**kw: Any) -> ServerConfig:
    return ServerConfig(resource="TCPIP0::x::INSTR", **kw)


# -- auth -----------------------------------------------------------------
def test_no_auth_mode_allows_request() -> None:
    with TestClient(_app(api_key=None)) as c:
        r = c.post("/mcp", content=_INIT, headers=_POST_HEADERS)
        assert r.status_code != 401


def test_auth_rejects_missing_bearer() -> None:
    with TestClient(_app(api_key="secret")) as c:
        r = c.post("/mcp", content=_INIT, headers=_POST_HEADERS)
        assert r.status_code == 401


def test_auth_rejects_wrong_bearer() -> None:
    with TestClient(_app(api_key="secret")) as c:
        r = c.post("/mcp", content=_INIT, headers={**_POST_HEADERS, "Authorization": "Bearer nope"})
        assert r.status_code == 401


def test_auth_allows_correct_bearer() -> None:
    with TestClient(_app(api_key="secret")) as c:
        r = c.post(
            "/mcp", content=_INIT, headers={**_POST_HEADERS, "Authorization": "Bearer secret"}
        )
        assert r.status_code != 401


# -- banner ---------------------------------------------------------------
def test_banner_plain_http() -> None:
    b = build_banner(_cfg(), "MSO5204", "MSO5000", 9)
    assert "TLS      : disabled" in b
    assert "API key  : disabled" in b
    assert "http://0.0.0.0:8080/mcp" in b
    assert "Authorization: Bearer" not in b


def test_banner_with_tls_and_key_never_prints_key_value() -> None:
    b = build_banner(
        _cfg(api_key="supersecretvalue", tls_cert="/c.pem", tls_key="/k.pem"),
        "MSO5204",
        "MSO5000",
        10,
    )
    assert "TLS      : enabled" in b
    assert "API key  : enabled" in b
    assert "https://0.0.0.0:8080/mcp" in b
    assert "Authorization: Bearer <your-api-key>" in b
    assert "supersecretvalue" not in b  # the real key value is never emitted
