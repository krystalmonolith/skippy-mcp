"""Phase 6 tests: end-to-end through the MCP server call path (no hardware).

These drive the real SDK request handler so input-schema validation and the
exception-to-isError mapping are exercised, all against SimulatedTransport.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp import types

from skippy_mcp.driver.session import establish
from skippy_mcp.mcp.server import build_server
from skippy_mcp.mcp.tools import build_tool_specs
from skippy_mcp.transport.simulated import SimulatedTransport


def _call(server: Any, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    handler = server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = asyncio.run(handler(req))
    return result.root


def _server(*, allow_raw: bool = False) -> Any:
    scope = establish(SimulatedTransport(), reset_on_connect=False)
    return build_server(scope, build_tool_specs(allow_raw), async_dispatch=False)


def test_list_tools_reports_nine() -> None:
    server = _server()
    handler = server.request_handlers[types.ListToolsRequest]
    req = types.ListToolsRequest(method="tools/list")
    result = asyncio.run(handler(req))
    assert len(result.root.tools) == 9


def test_end_to_end_configure_capture_measure_screenshot() -> None:
    server = _server()

    ident = _call(server, "get_identity", {})
    assert ident.isError is False
    assert ident.structuredContent is not None
    assert ident.structuredContent["model"] == "MSO5204"

    cfg = _call(
        server, "configure_channel", {"channel": 1, "enabled": True, "scale_v_per_div": 0.5}
    )
    assert cfg.isError is False

    cap = _call(server, "capture", {"action": "single"})
    assert cap.isError is False

    meas = _call(server, "measure", {"type": "vpp", "source": "CH1"})
    assert meas.isError is False
    assert meas.structuredContent is not None
    assert meas.structuredContent["value"] > 0

    shot = _call(server, "screenshot", {"format": "png"})
    assert shot.isError is False
    assert shot.content[0].type == "image"


def test_schema_validation_rejects_bad_channel() -> None:
    server = _server()
    result = _call(server, "configure_channel", {"channel": 9})  # exceeds maximum=4
    assert result.isError is True
    assert "validation" in result.content[0].text.lower()


def test_driver_validation_surfaces_actionable_error() -> None:
    # Passes schema (number > 0) but exceeds the device's max scale -> driver rejects.
    server = _server()
    result = _call(server, "configure_channel", {"channel": 1, "scale_v_per_div": 999.0})
    assert result.isError is True
    text = result.content[0].text
    assert "configure_channel" in text and "Check:" in text


def test_scpi_raw_absent_unless_allowed() -> None:
    server = _server(allow_raw=False)
    result = _call(server, "scpi_raw", {"command": "*IDN?"})
    assert result.isError is True  # unknown tool

    allowed = _server(allow_raw=True)
    ok = _call(allowed, "scpi_raw", {"command": "*IDN?"})
    assert ok.isError is False
    assert ok.structuredContent is not None
    assert "MSO5204" in ok.structuredContent["response"]
