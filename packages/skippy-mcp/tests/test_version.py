"""__version__ must track the installed distribution metadata, never a hardcoded
literal that drifts from pyproject (regression for the 0.3.0-everywhere banner bug)."""

from __future__ import annotations

from importlib.metadata import version

import skippy_mcp


def test_version_matches_distribution_metadata() -> None:
    assert skippy_mcp.__version__ == version("skippy-mcp")
