"""MCP server wiring.

Adapts the SDK-agnostic tool specs in :mod:`skippy_mcp.mcp.tools` onto the MCP
low-level :class:`Server`. The SDK validates tool input against each spec's
``inputSchema`` and converts handler exceptions to ``isError`` results, so a
raised :class:`SkippyError` surfaces its actionable message directly. Unexpected
exceptions are logged with a traceback and replaced with a generic actionable
message so internals never leak.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from skippy_mcp.config import parse_args
from skippy_mcp.core.errors import SkippyError
from skippy_mcp.driver.scope import Scope
from skippy_mcp.driver.session import establish, open_transport
from skippy_mcp.mcp.tools import ToolOutput, ToolSpec, build_tool_specs

logger = logging.getLogger("skippy_mcp")


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


def build_server(scope: Scope, specs: list[ToolSpec], *, async_dispatch: bool) -> Server:
    """Construct an MCP server exposing ``specs`` backed by ``scope``."""
    server: Server = Server("skippy-mcp")
    by_name = {spec.name: spec for spec in specs}

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
        try:
            if async_dispatch:
                output = await anyio.to_thread.run_sync(spec.handler, scope, arguments)
            else:
                output = spec.handler(scope, arguments)
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


async def _serve(server: Server) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Console-script entry point."""
    logging.basicConfig(level=logging.INFO)  # stderr; stdout is the MCP channel.
    config = parse_args()
    transport = open_transport(config)
    scope = establish(transport, reset_on_connect=config.reset_on_connect)
    specs = build_tool_specs(config.allow_raw_scpi)
    server = build_server(scope, specs, async_dispatch=config.async_dispatch)
    logger.info(
        "SkippyMCP serving %s (%s), %d tools, %s dispatch",
        scope.identify().model,
        scope.dialect_series,
        len(specs),
        "async" if config.async_dispatch else "sync",
    )
    anyio.run(_serve, server)


if __name__ == "__main__":
    main()
