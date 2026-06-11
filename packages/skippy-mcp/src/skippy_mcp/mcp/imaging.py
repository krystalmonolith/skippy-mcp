"""Image transcoding for delivery over MCP.

Scopes capture in their native format (BMP for MSO5000); this converts to PNG so
the payload base64'd into the model's context is ~30x smaller. This is a
presentation concern and lives in the MCP layer, not the driver.
"""

from __future__ import annotations

import io

from PIL import Image

from skippy_mcp.core.enums import ImageFormat


def to_png(data: bytes, source_format: ImageFormat) -> bytes:
    """Return ``data`` as PNG bytes, transcoding from ``source_format`` if needed."""
    if source_format is ImageFormat.PNG:
        return data
    with Image.open(io.BytesIO(data)) as img:
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
