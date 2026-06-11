"""SkippyMCP dialect layer.

Importing this package registers all concrete dialects so :func:`select_dialect`
can find them.
"""

from __future__ import annotations

from skippy_mcp.dialect.base import Dialect, select_dialect, supported_series
from skippy_mcp.dialect.mso5000 import MSO5000Dialect

__all__ = ["Dialect", "MSO5000Dialect", "select_dialect", "supported_series"]
