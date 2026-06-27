"""StimpyMCP — an MCP server that drives a GPIO digital-pattern stimulus generator
on a Raspberry Pi, via the kernel GPIO chardev (lgpio). Sibling to SkippyMCP."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the installed distribution version (pyproject).
    __version__ = version("stimpy-mcp")
except PackageNotFoundError:  # not installed (e.g. running straight from a source tree)
    __version__ = "0+unknown"
