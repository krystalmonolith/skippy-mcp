"""MCP tool specifications and handlers.

This module is deliberately free of any MCP-SDK imports: each tool is a plain
handler ``(Scope, args) -> ToolOutput`` plus its JSON Schema. ``server.py`` adapts
these to the SDK. Keeping the logic here means the full tool surface is unit-
testable against the simulator with no client and no event loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

from skippy_mcp.core.enums import (
    BandwidthLimit,
    BusFormat,
    BusProtocol,
    CaptureAction,
    Coupling,
    I2cAddressMode,
    ImageFormat,
    MeasurementType,
    TriggerMode,
    TriggerSlope,
    WaveformMode,
)
from skippy_mcp.core.errors import ValidationError
from skippy_mcp.core.models import (
    BusConfig,
    ChannelConfig,
    LogicConfig,
    Screenshot,
    TriggerConfig,
)
from skippy_mcp.driver.scope import Scope
from skippy_mcp.mcp.imaging import to_png

_E = TypeVar("_E", bound=Enum)


@dataclass(frozen=True, slots=True)
class ToolOutput:
    """Result of a tool handler: structured JSON, an image, or both."""

    structured: dict[str, Any] | None = None
    image: Screenshot | None = None


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool's name, description, JSON Schema, and handler."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[Scope, dict[str, Any]], ToolOutput]
    gated: bool = False


def _enum(enum_cls: type[_E], value: object, operation: str, parameter: str) -> _E:
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = [e.value for e in enum_cls]
        raise ValidationError(
            operation, parameter=parameter, value=value, requirement=f"one of {allowed}"
        ) from exc


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    return [str(e.value) for e in enum_cls]


# -- handlers -------------------------------------------------------------
def _h_get_identity(scope: Scope, args: dict[str, Any]) -> ToolOutput:
    idn = scope.identify()
    return ToolOutput(
        structured={
            "manufacturer": idn.manufacturer,
            "model": idn.model,
            "serial": idn.serial,
            "firmware": idn.firmware,
            "dialect": scope.dialect_series,
        }
    )


def _h_configure_channel(scope: Scope, args: dict[str, Any]) -> ToolOutput:
    op = "configure_channel"
    coupling = args.get("coupling")
    bandwidth = args.get("bandwidth_limit")
    config = ChannelConfig(
        channel=int(args["channel"]),
        enabled=args.get("enabled"),
        scale_v_per_div=args.get("scale_v_per_div"),
        offset_v=args.get("offset_v"),
        coupling=_enum(Coupling, coupling, op, "coupling") if coupling is not None else None,
        bandwidth_limit=(
            _enum(BandwidthLimit, bandwidth, op, "bandwidth_limit")
            if bandwidth is not None
            else None
        ),
        probe_ratio=args.get("probe_ratio"),
    )
    scope.configure_channel(config)
    return ToolOutput(structured={"status": "ok", "channel": config.channel})


def _h_configure_logic(scope: Scope, args: dict[str, Any]) -> ToolOutput:
    config = LogicConfig(
        channels=tuple(int(c) for c in args["channels"]),
        enabled=args.get("enabled"),
        threshold_v=args.get("threshold_v"),
        label=args.get("label"),
    )
    scope.configure_logic(config)
    structured: dict[str, Any] = {"status": "ok", "channels": list(config.channels)}
    if args.get("label") is not None:
        # Accepted for forward-compatibility but not yet emitted to the scope.
        structured["unimplemented"] = ["label"]
    return ToolOutput(structured=structured)


def _h_configure_trigger(scope: Scope, args: dict[str, Any]) -> ToolOutput:
    op = "configure_trigger"
    mode = _enum(TriggerMode, args["mode"], op, "mode")
    if mode is not TriggerMode.EDGE:
        # Only edge-trigger configuration is implemented; report rather than
        # silently applying a partial setup or ignoring the request.
        return ToolOutput(
            structured={
                "status": "unimplemented",
                "mode": mode.value,
                "implemented": ["edge"],
                "detail": (
                    f"detailed configuration for the {mode.value!r} trigger is not yet "
                    "implemented; only 'edge' is fully supported"
                ),
            }
        )
    slope = args.get("slope")
    config = TriggerConfig(
        mode=mode,
        source=args.get("source"),
        slope=_enum(TriggerSlope, slope, op, "slope") if slope is not None else None,
        level_v=args.get("level_v"),
        pattern=args.get("pattern", {}),
    )
    scope.configure_trigger(config)
    return ToolOutput(structured={"status": "ok", "mode": config.mode.value})


def _h_capture(scope: Scope, args: dict[str, Any]) -> ToolOutput:
    action = _enum(CaptureAction, args["action"], "capture", "action")
    scope.capture(action)
    return ToolOutput(structured={"status": "ok", "action": action.value})


def _h_measure(scope: Scope, args: dict[str, Any]) -> ToolOutput:
    mtype = _enum(MeasurementType, args["type"], "measure", "type")
    result = scope.measure(mtype, str(args["source"]), args.get("source2"))
    return ToolOutput(
        structured={
            "type": result.type.value,
            "source": result.source,
            "value": result.value,
            "unit": result.unit,
        }
    )


def _h_screenshot(scope: Scope, args: dict[str, Any]) -> ToolOutput:
    shot = scope.screenshot()  # native format (BMP on MSO5000)
    png = to_png(shot.data, shot.image_format)
    return ToolOutput(image=Screenshot(image_format=ImageFormat.PNG, data=png))


def _h_read_waveform(scope: Scope, args: dict[str, Any]) -> ToolOutput:
    mode_value = args.get("mode", WaveformMode.NORMAL.value)
    mode = _enum(WaveformMode, mode_value, "read_waveform", "mode")
    wave = scope.read_waveform(
        str(args["source"]), mode=mode, max_points=int(args.get("max_points", 1200))
    )
    return ToolOutput(
        structured={
            "source": wave.source,
            "x_unit": wave.x_unit,
            "y_unit": wave.y_unit,
            "x_increment": wave.x_increment,
            "x_origin": wave.x_origin,
            "values": list(wave.values),
        }
    )


def _h_decode_bus(scope: Scope, args: dict[str, Any]) -> ToolOutput:
    protocol = _enum(BusProtocol, args["protocol"], "decode_bus", "protocol")
    address_mode = (
        _enum(I2cAddressMode, args["address_mode"], "decode_bus", "address_mode")
        if "address_mode" in args
        else None
    )
    fmt = _enum(BusFormat, args["format"], "decode_bus", "format") if "format" in args else None
    config = BusConfig(
        bus=int(args["bus"]),
        protocol=protocol,
        scl_source=args.get("scl_source"),
        sda_source=args.get("sda_source"),
        scl_threshold_v=args.get("scl_threshold_v"),
        sda_threshold_v=args.get("sda_threshold_v"),
        address_mode=address_mode,
        fmt=fmt,
        options=args.get("config", {}),
    )
    frames = scope.decode_bus(config)
    structured: dict[str, Any] = {
        "frames": [{"time": f.time, "label": f.label, "data": f.data} for f in frames]
    }
    if args.get("config"):
        # The free-form `config` passthrough is for keys not yet first-class;
        # the typed scl/sda/threshold/address_mode/format fields ARE applied.
        structured["unimplemented"] = ["config"]
    return ToolOutput(structured=structured)


def _h_scpi_raw(scope: Scope, args: dict[str, Any]) -> ToolOutput:
    response = scope.raw_scpi(str(args["command"]), args.get("expect_response"))
    return ToolOutput(structured={"response": response})


# -- schemas --------------------------------------------------------------
def _obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def build_tool_specs(allow_raw_scpi: bool) -> list[ToolSpec]:
    """Return the tool specs; ``scpi_raw`` is included only when allowed."""
    source_pattern = "^(CH[1-4]|D([0-9]|1[0-5]))$"
    specs: list[ToolSpec] = [
        ToolSpec(
            "get_identity",
            "Report the connected instrument's identity and selected dialect.",
            _obj({}, []),
            _h_get_identity,
        ),
        ToolSpec(
            "configure_channel",
            "Configure an analog channel (enable, scale, offset, coupling, etc.).",
            _obj(
                {
                    "channel": {"type": "integer", "minimum": 1, "maximum": 4},
                    "enabled": {"type": "boolean"},
                    "scale_v_per_div": {"type": "number", "exclusiveMinimum": 0},
                    "offset_v": {"type": "number"},
                    "coupling": {"type": "string", "enum": _enum_values(Coupling)},
                    "bandwidth_limit": {"type": "string", "enum": _enum_values(BandwidthLimit)},
                    "probe_ratio": {"type": "number", "exclusiveMinimum": 0},
                },
                ["channel"],
            ),
            _h_configure_channel,
        ),
        ToolSpec(
            "configure_logic",
            "Configure digital channels D0-D15 (enable, threshold). 'label' is accepted "
            "but not yet applied (reported as unimplemented).",
            _obj(
                {
                    "channels": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0, "maximum": 15},
                        "minItems": 1,
                    },
                    "enabled": {"type": "boolean"},
                    "threshold_v": {"type": "number"},
                    "label": {"type": "string"},
                },
                ["channels"],
            ),
            _h_configure_logic,
        ),
        ToolSpec(
            "configure_trigger",
            "Configure the trigger. Only 'edge' mode is fully implemented; 'pulse'/'pattern' "
            "return an 'unimplemented' status.",
            _obj(
                {
                    "mode": {"type": "string", "enum": _enum_values(TriggerMode)},
                    "source": {"type": "string", "pattern": source_pattern},
                    "slope": {"type": "string", "enum": _enum_values(TriggerSlope)},
                    "level_v": {"type": "number"},
                    "pattern": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                ["mode"],
            ),
            _h_configure_trigger,
        ),
        ToolSpec(
            "capture",
            "Control acquisition: run, stop, or single.",
            _obj({"action": {"type": "string", "enum": _enum_values(CaptureAction)}}, ["action"]),
            _h_capture,
        ),
        ToolSpec(
            "measure",
            "Read an automated measurement from a source.",
            _obj(
                {
                    "type": {"type": "string", "enum": _enum_values(MeasurementType)},
                    "source": {"type": "string", "pattern": "^CH[1-4]$"},
                    "source2": {"type": "string", "pattern": "^CH[1-4]$"},
                },
                ["type", "source"],
            ),
            _h_measure,
        ),
        ToolSpec(
            "screenshot",
            "Capture the instrument display as a PNG image.",
            _obj({}, []),
            _h_screenshot,
        ),
        ToolSpec(
            "read_waveform",
            "Read a waveform trace; large captures are downsampled to max_points.",
            _obj(
                {
                    "source": {"type": "string", "pattern": source_pattern},
                    "mode": {"type": "string", "enum": _enum_values(WaveformMode)},
                    "max_points": {"type": "integer", "minimum": 1},
                },
                ["source"],
            ),
            _h_read_waveform,
        ),
        ToolSpec(
            "decode_bus",
            "Configure and read a hardware bus decode. For I2C, assign the SCL/SDA "
            "source channels, decode thresholds, and address mode, then read the "
            "decoded frames. The free-form 'config' object is a passthrough for keys "
            "not yet first-class (reported as unimplemented).",
            _obj(
                {
                    "bus": {"type": "integer", "minimum": 1, "maximum": 2},
                    "protocol": {"type": "string", "enum": _enum_values(BusProtocol)},
                    "scl_source": {"type": "string", "pattern": source_pattern},
                    "sda_source": {"type": "string", "pattern": source_pattern},
                    "scl_threshold_v": {"type": "number"},
                    "sda_threshold_v": {"type": "number"},
                    "address_mode": {"type": "string", "enum": _enum_values(I2cAddressMode)},
                    "format": {"type": "string", "enum": _enum_values(BusFormat)},
                    "config": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                ["bus", "protocol"],
            ),
            _h_decode_bus,
        ),
    ]
    if allow_raw_scpi:
        specs.append(
            ToolSpec(
                "scpi_raw",
                "Send an arbitrary SCPI command/query (escape hatch).",
                _obj(
                    {
                        "command": {"type": "string", "minLength": 1},
                        "expect_response": {"type": "boolean"},
                    },
                    ["command"],
                ),
                _h_scpi_raw,
                gated=True,
            )
        )
    return specs
