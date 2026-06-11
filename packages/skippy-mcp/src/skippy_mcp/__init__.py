"""SkippyMCP — an MCP server for controlling Rigol oscilloscopes via SCPI/PyVISA."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the installed distribution version (pyproject).
    __version__ = version("skippy-mcp")
except PackageNotFoundError:  # not installed (e.g. running straight from a source tree)
    __version__ = "0+unknown"
