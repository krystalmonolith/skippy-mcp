"""Phase 0 smoke test: the package imports and reports its packaged version."""

from importlib.metadata import version

import skippy_mcp


def test_version_is_present() -> None:
    # __version__ derives from the installed distribution metadata (pyproject),
    # so assert that wiring holds rather than hardcoding a number that drifts.
    assert skippy_mcp.__version__ == version("skippy-mcp")
    assert skippy_mcp.__version__ != "0+unknown"
