"""Domain value objects.

All types here are pure data: frozen dataclasses with no I/O and no dependency
on the transport, dialect, or MCP layers. ``Optional`` fields on the ``*Config``
carriers mean "leave unchanged" so a tool can apply a partial update.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from skippy_mcp.core.enums import (
    BandwidthLimit,
    BusProtocol,
    Coupling,
    ImageFormat,
    MeasurementType,
    TriggerMode,
    TriggerSlope,
)


@dataclass(frozen=True, slots=True)
class IdnInfo:
    """Parsed response of the ``*IDN?`` query."""

    manufacturer: str
    model: str
    serial: str
    firmware: str

    @classmethod
    def parse(cls, raw: str) -> IdnInfo:
        """Parse a ``manufacturer,model,serial,firmware`` identity string.

        Extra trailing fields are ignored; missing fields become empty strings.
        """
        parts = [p.strip() for p in raw.strip().split(",")]
        parts += [""] * (4 - len(parts))
        return cls(manufacturer=parts[0], model=parts[1], serial=parts[2], firmware=parts[3])


@dataclass(frozen=True, slots=True)
class DeviceLimits:
    """Per-model bounds used for pre-validation."""

    num_analog_channels: int
    num_digital_channels: int
    min_scale_v_per_div: float
    max_scale_v_per_div: float


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    """Desired analog-channel configuration; ``None`` fields are left unchanged."""

    channel: int
    enabled: bool | None = None
    scale_v_per_div: float | None = None
    offset_v: float | None = None
    coupling: Coupling | None = None
    bandwidth_limit: BandwidthLimit | None = None
    probe_ratio: float | None = None


@dataclass(frozen=True, slots=True)
class LogicConfig:
    """Desired digital-channel configuration for one or more D-channels."""

    channels: tuple[int, ...]
    enabled: bool | None = None
    threshold_v: float | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class TriggerConfig:
    """Desired trigger configuration.

    Which optional fields are meaningful depends on ``mode``: ``slope``/``level_v``
    for edge, ``pattern`` for pattern, etc. The driver validates the combination.
    """

    mode: TriggerMode
    source: str | None = None
    slope: TriggerSlope | None = None
    level_v: float | None = None
    pattern: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    """Result of an automated measurement."""

    type: MeasurementType
    source: str
    value: float
    unit: str


@dataclass(frozen=True, slots=True)
class WaveformData:
    """A captured waveform trace."""

    source: str
    x_unit: str
    y_unit: str
    x_increment: float
    x_origin: float
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Screenshot:
    """A screen capture of the instrument display."""

    image_format: ImageFormat
    data: bytes


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    """One decoded frame from a bus-decode operation."""

    time: float
    data: str
    label: str


@dataclass(frozen=True, slots=True)
class BusConfig:
    """Desired bus-decode configuration."""

    bus: int
    protocol: BusProtocol
    options: dict[str, str] = field(default_factory=dict)
