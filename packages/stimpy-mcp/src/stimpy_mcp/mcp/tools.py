"""MCP tool specifications and handlers.

Deliberately free of MCP-SDK imports: each tool is a plain handler
``(StimulusDriver, args) -> ToolOutput`` plus its JSON Schema. ``server.py``
adapts these to the SDK. The full surface is unit-testable against the simulator
with no client and no event loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import rig_contract

from stimpy_mcp.core.enums import RunMode
from stimpy_mcp.core.errors import ValidationError
from stimpy_mcp.core.models import EngineStatus, clock_to_tick_us
from stimpy_mcp.driver.stimulus import StimulusDriver


@dataclass(frozen=True, slots=True)
class ToolOutput:
    """Result of a tool handler: structured JSON."""

    structured: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool's name, description, JSON Schema, and handler."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[StimulusDriver, dict[str, Any]], ToolOutput]
    gated: bool = False


def _obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _status_dict(s: EngineStatus) -> dict[str, Any]:
    return {
        "state": s.state.value,
        "running": s.running,
        "clock_rate_hz": s.clock_rate_hz,
        "actual_clock_rate_hz": s.actual_clock_rate_hz,
        "tick_us": s.tick_us,
        "buffer_frames": s.buffer_frames,
        "frames_emitted": s.frames_emitted,
        "loops_completed": s.loops_completed,
        "current_frame": s.current_frame,
        "limits": {
            "max_pulses": s.limits.max_pulses,
            "max_cbs": s.limits.max_cbs,
            "min_tick_us": s.limits.min_tick_us,
            "max_frames_per_wave": s.limits.max_frames_per_wave,
        },
    }


# -- handlers -------------------------------------------------------------
def _h_get_status(driver: StimulusDriver, args: dict[str, Any]) -> ToolOutput:
    return ToolOutput(structured=_status_dict(driver.status()))


def _h_get_pin_map(driver: StimulusDriver, args: dict[str, Any]) -> ToolOutput:
    pm = driver.pin_map
    return ToolOutput(
        structured={
            "data_pins": list(pm.data_pins),
            "sync_pin": pm.sync_pin,
            "channel_count": pm.channel_count,
        }
    )


def _h_set_pattern(driver: StimulusDriver, args: dict[str, Any]) -> ToolOutput:
    status = driver.set_pattern(
        args["frames"],
        sync_frames=args.get("sync_frames"),
        clock_rate_hz=args.get("clock_rate_hz"),
        go_live=bool(args.get("go_live", True)),
    )
    out = _status_dict(status)
    out["status"] = "live" if status.running else "staged"
    return ToolOutput(structured=out)


def _h_set_clock_rate(driver: StimulusDriver, args: dict[str, Any]) -> ToolOutput:
    requested = args["clock_rate_hz"]
    status = driver.set_clock_rate(requested)
    out = _status_dict(status)
    # The new rate swaps in at the next loop boundary, so live status may still
    # show the old clock. Report the requested rate's quantization explicitly.
    tick = clock_to_tick_us(requested)
    out["requested_clock_rate_hz"] = requested
    out["new_tick_us"] = tick
    out["new_actual_clock_rate_hz"] = 1_000_000.0 / tick
    return ToolOutput(structured=out)


def _h_start(driver: StimulusDriver, args: dict[str, Any]) -> ToolOutput:
    raw = args.get("mode", "repeat")
    try:
        mode = RunMode(raw)
    except ValueError as exc:
        raise ValidationError(
            "start", parameter="mode", value=raw,
            requirement=f"one of {[m.value for m in RunMode]}",
        ) from exc
    return ToolOutput(structured=_status_dict(driver.start(mode)))


def _h_stop(driver: StimulusDriver, args: dict[str, Any]) -> ToolOutput:
    status = driver.stop()
    out = _status_dict(status)
    out["status"] = "stopped"
    return ToolOutput(structured=out)


def _h_load_counter(driver: StimulusDriver, args: dict[str, Any]) -> ToolOutput:
    bits = int(args.get("bits", rig_contract.CHANNEL_COUNT))
    sync_period = int(args.get("sync_period", 1 << bits))
    if not 1 <= bits <= rig_contract.CHANNEL_COUNT:
        raise ValidationError(
            "load_counter", parameter="bits", value=bits,
            requirement=f"1 <= bits <= {rig_contract.CHANNEL_COUNT}",
        )
    if sync_period < 1:
        raise ValidationError(
            "load_counter", parameter="sync_period", value=sync_period, requirement=">= 1"
        )
    frames = rig_contract.counter_frames(bits)
    sync_frames = rig_contract.counter_sync_frames(bits, sync_period)
    status = driver.set_pattern(frames, sync_frames=sync_frames, go_live=True)
    out = _status_dict(status)
    out["status"] = "live" if status.running else "staged"
    out["bits"] = bits
    return ToolOutput(structured=out)


# -- registry -------------------------------------------------------------
def build_tool_specs(allow_builtin_patterns: bool = False) -> list[ToolSpec]:
    """The tool surface; the gated load_counter is added only when enabled."""
    word_max = rig_contract.MASK
    cc = rig_contract.CHANNEL_COUNT
    specs: list[ToolSpec] = [
        ToolSpec(
            name="get_status",
            description="Report engine run state, clock, frame counts, and device limits.",
            input_schema=_obj({}, []),
            handler=_h_get_status,
        ),
        ToolSpec(
            name="get_pin_map",
            description="Report the BCM GPIO pin assignment for D0..D15 and SYNC.",
            input_schema=_obj({}, []),
            handler=_h_get_pin_map,
        ),
        ToolSpec(
            name="set_pattern",
            description=(
                "Upload a digital pattern buffer (one 16-bit word per frame). SYNC pulses on "
                "frame 0 by default. By default it goes live at the next sync-frame boundary "
                "(glitch-free). Bit i of each word drives channel D<i>."
            ),
            input_schema=_obj(
                {
                    "frames": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0, "maximum": word_max},
                        "minItems": 1,
                        "description": f"16-bit words, 0..0x{word_max:X}; bit i -> D<i>.",
                    },
                    "sync_frames": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0},
                        "description": "Frame indices that pulse SYNC high (default [0]).",
                    },
                    "clock_rate_hz": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "Frame clock in Hz (D0 = clock/2); default: server clock.",
                    },
                    "go_live": {
                        "type": "boolean",
                        "description": "Activate at the next sync frame (default true).",
                    },
                },
                ["frames"],
            ),
            handler=_h_set_pattern,
        ),
        ToolSpec(
            name="set_clock_rate",
            description=(
                "Set the frame clock in Hz; restages and swaps the current pattern if running. "
                "Quantized to whole microseconds; the achieved rate is reported back."
            ),
            input_schema=_obj(
                {"clock_rate_hz": {"type": "number", "exclusiveMinimum": 0}},
                ["clock_rate_hz"],
            ),
            handler=_h_set_clock_rate,
        ),
        ToolSpec(
            name="start",
            description="Activate the staged pattern (repeat or once). Errors if none is staged.",
            input_schema=_obj(
                {"mode": {"type": "string", "enum": [m.value for m in RunMode]}}, []
            ),
            handler=_h_start,
        ),
        ToolSpec(
            name="stop",
            description="Halt transmission and drive all lines low.",
            input_schema=_obj({}, []),
            handler=_h_stop,
        ),
    ]
    if allow_builtin_patterns:
        specs.append(
            ToolSpec(
                name="load_counter",
                description=(
                    "Stage and run the built-in binary up-counter (D0=LSB). bits<=16; "
                    "SYNC pulses every sync_period counts."
                ),
                input_schema=_obj(
                    {
                        "bits": {"type": "integer", "minimum": 1, "maximum": cc},
                        "sync_period": {"type": "integer", "minimum": 1},
                    },
                    [],
                ),
                handler=_h_load_counter,
                gated=True,
            )
        )
    return specs
