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


def _cfg(**kw: Any) -> ServerConfig:
    return ServerConfig(resource="TCPIP0::x::INSTR", **kw)


def _app(api_key: str | None = None, **cfg_kw: Any) -> Any:
    scope = establish(SimulatedTransport(), reset_on_connect=False)
    server = build_mcp_server(scope, build_tool_specs(allow_raw_scpi=False))
    return build_app(server, _cfg(api_key=api_key, **cfg_kw))


def _client(app: Any) -> TestClient:
    # Host 127.0.0.1:8080 matches the default DNS-rebinding allow-list.
    return TestClient(app, base_url="http://127.0.0.1:8080")


# -- auth -----------------------------------------------------------------
def test_no_auth_mode_allows_request() -> None:
    with _client(_app(api_key=None)) as c:
        r = c.post("/mcp", content=_INIT, headers=_POST_HEADERS)
        assert r.status_code != 401


def test_auth_rejects_missing_bearer() -> None:
    with _client(_app(api_key="secret")) as c:
        r = c.post("/mcp", content=_INIT, headers=_POST_HEADERS)
        assert r.status_code == 401


def test_auth_rejects_wrong_bearer() -> None:
    with _client(_app(api_key="secret")) as c:
        r = c.post("/mcp", content=_INIT, headers={**_POST_HEADERS, "Authorization": "Bearer nope"})
        assert r.status_code == 401


def test_auth_allows_correct_bearer() -> None:
    with _client(_app(api_key="secret")) as c:
        r = c.post(
            "/mcp", content=_INIT, headers={**_POST_HEADERS, "Authorization": "Bearer secret"}
        )
        assert r.status_code != 401


# -- DNS-rebinding protection (H3) ----------------------------------------
def test_disallowed_host_is_rejected() -> None:
    # A forged/unknown Host (DNS-rebinding) is refused with 421 before dispatch.
    with TestClient(_app(api_key=None), base_url="http://evil.example.com") as c:
        r = c.post("/mcp", content=_INIT, headers=_POST_HEADERS)
        assert r.status_code == 421


def test_allowed_host_passes() -> None:
    with _client(_app(api_key=None)) as c:
        r = c.post("/mcp", content=_INIT, headers=_POST_HEADERS)
        assert r.status_code != 421


def test_extra_allowed_host_from_config() -> None:
    app = _app(api_key=None, allowed_hosts=("scope.lan:8080",))
    with TestClient(app, base_url="http://scope.lan:8080") as c:
        r = c.post("/mcp", content=_INIT, headers=_POST_HEADERS)
        assert r.status_code != 421


def test_auth_rejected_before_host_check() -> None:
    # Bad bearer + bad Host -> 401 (auth wraps the transport security check).
    with TestClient(_app(api_key="secret"), base_url="http://evil.example.com") as c:
        r = c.post("/mcp", content=_INIT, headers=_POST_HEADERS)
        assert r.status_code == 401


# -- banner ---------------------------------------------------------------
def test_banner_plain_http() -> None:
    b = build_banner(_cfg(), "MSO5204", "MSO5000", 9)
    assert "TLS      : disabled" in b
    assert "API key  : disabled" in b
    assert "http://127.0.0.1:8080/mcp" in b
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
    assert "https://127.0.0.1:8080/mcp" in b
    assert "Authorization: Bearer <your-api-key>" in b
    assert "supersecretvalue" not in b  # the real key value is never emitted


# -- startup warnings (H1 / H4) -------------------------------------------
def test_warns_on_nonloopback_bind_without_auth() -> None:
    from skippy_mcp.mcp.server import startup_warnings

    warnings = startup_warnings(_cfg(bind="0.0.0.0"))
    assert any("NO authentication" in w for w in warnings)


def test_warns_on_api_key_without_tls() -> None:
    from skippy_mcp.mcp.server import startup_warnings

    warnings = startup_warnings(_cfg(api_key="secret"))
    assert any("cleartext" in w for w in warnings)


def test_no_warnings_on_loopback_no_auth() -> None:
    from skippy_mcp.mcp.server import startup_warnings

    assert startup_warnings(_cfg()) == []


# -- shutdown releases the instrument link --------------------------------
def test_scope_closed_on_app_shutdown() -> None:
    transport = SimulatedTransport()
    scope = establish(transport, reset_on_connect=False)
    server = build_mcp_server(scope, build_tool_specs(allow_raw_scpi=False))
    app = build_app(server, _cfg(), scope=scope)
    assert transport.closed is False
    with _client(app):
        pass  # entering/exiting the client runs the lifespan startup + shutdown
    assert transport.closed is True
