"""Phase 5 tests: MCP tool specs, handlers, schemas, and output conversion."""

from __future__ import annotations

import jsonschema
import pytest

from skippy_mcp.core.errors import ValidationError
from skippy_mcp.driver.scope import Scope
from skippy_mcp.driver.session import establish
from skippy_mcp.mcp.server import build_mcp_server, convert_output
from skippy_mcp.mcp.tools import ToolOutput, build_tool_specs
from skippy_mcp.transport.simulated import SimulatedTransport


@pytest.fixture
def scope() -> Scope:
    return establish(SimulatedTransport(), reset_on_connect=False)


def _spec(name: str, allow_raw: bool = False):  # type: ignore[no-untyped-def]
    return next(s for s in build_tool_specs(allow_raw) if s.name == name)


# -- spec set -------------------------------------------------------------
def test_default_tool_set_has_nine_tools_without_raw() -> None:
    specs = build_tool_specs(allow_raw_scpi=False)
    names = {s.name for s in specs}
    assert "scpi_raw" not in names
    assert len(specs) == 9


def test_raw_tool_registered_only_when_allowed() -> None:
    assert any(s.name == "scpi_raw" for s in build_tool_specs(allow_raw_scpi=True))


def test_every_input_schema_is_valid_json_schema() -> None:
    for spec in build_tool_specs(allow_raw_scpi=True):
        jsonschema.Draft202012Validator.check_schema(spec.input_schema)


# -- handlers -------------------------------------------------------------
def test_get_identity_handler(scope: Scope) -> None:
    out = _spec("get_identity").handler(scope, {})
    assert out.structured == {
        "manufacturer": "RIGOL TECHNOLOGIES",
        "model": "MSO5204",
        "serial": "SIM00000000",
        "firmware": "00.01.03.02.02",
        "dialect": "MSO5000",
    }


def test_configure_channel_handler(scope: Scope) -> None:
    out = _spec("configure_channel").handler(
        scope, {"channel": 1, "enabled": True, "coupling": "ac"}
    )
    assert out.structured == {"status": "ok", "channel": 1}


def test_measure_handler_returns_value_and_unit(scope: Scope) -> None:
    out = _spec("measure").handler(scope, {"type": "vpp", "source": "CH1"})
    assert out.structured is not None
    assert out.structured["value"] == pytest.approx(3.3)
    assert out.structured["unit"] == "V"


def test_screenshot_handler_transcodes_to_png(scope: Scope) -> None:
    # Scope captures BMP natively; the handler must deliver PNG.
    out = _spec("screenshot").handler(scope, {})
    assert out.image is not None
    assert out.image.image_format.value == "png"
    assert out.image.data.startswith(b"\x89PNG")


def test_to_png_transcodes_bmp_to_png() -> None:
    from skippy_mcp.core.enums import ImageFormat
    from skippy_mcp.mcp.imaging import to_png
    from skippy_mcp.transport.simulated import SimulatedTransport

    bmp = SimulatedTransport().query_binary(":DISPlay:DATA?")
    assert bmp.startswith(b"BM")
    png = to_png(bmp, ImageFormat.BMP)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_bad_enum_value_raises_validation_error(scope: Scope) -> None:
    with pytest.raises(ValidationError):
        _spec("configure_channel").handler(scope, {"channel": 1, "coupling": "bogus"})


# -- output conversion ----------------------------------------------------
def test_convert_structured_output_returns_dict() -> None:
    assert convert_output(ToolOutput(structured={"a": 1})) == {"a": 1}


def test_convert_image_output_returns_content_list() -> None:
    out = _spec("screenshot").handler(
        establish(SimulatedTransport(), reset_on_connect=False), {"format": "png"}
    )
    blocks = convert_output(out)
    assert isinstance(blocks, list)
    assert blocks[0].type == "image"
    assert blocks[0].mimeType == "image/png"


# -- build_server ---------------------------------------------------------
def test_build_mcp_server_constructs(scope: Scope) -> None:
    specs = build_tool_specs(allow_raw_scpi=False)
    server = build_mcp_server(scope, specs)
    assert server.name == "skippy-mcp"
