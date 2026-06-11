"""Phase 0 smoke test: the package imports and reports its version."""

import skippy_mcp


def test_version_is_present() -> None:
    assert skippy_mcp.__version__ == "0.3.0"
