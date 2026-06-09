"""The Scope driver: semantic operations over a dialect + transport.

Each method (1) pre-validates its inputs against the device limits, (2) emits
SCPI via the dialect, and (3) for state-changing operations, checks the scope's
``:SYSTem:ERRor?`` queue. Returns are domain types from ``core.models``.
"""

from __future__ import annotations

import logging

from skippy_mcp.core.enums import (
    CaptureAction,
    MeasurementType,
    TriggerMode,
    WaveformMode,
)
from skippy_mcp.core.errors import ScpiError, SkippyError, ValidationError
from skippy_mcp.core.models import (
    BusConfig,
    ChannelConfig,
    DecodedFrame,
    IdnInfo,
    LogicConfig,
    MeasurementResult,
    Screenshot,
    TriggerConfig,
    WaveformData,
)
from skippy_mcp.dialect.base import Dialect
from skippy_mcp.transport.base import Transport

# Measurement units by type.
_UNITS: dict[MeasurementType, str] = {
    MeasurementType.VPP: "V",
    MeasurementType.VRMS: "V",
    MeasurementType.FREQ: "Hz",
    MeasurementType.PERIOD: "s",
    MeasurementType.DUTY: "%",
    MeasurementType.RISE: "s",
    MeasurementType.FALL: "s",
    MeasurementType.DELAY: "s",
    MeasurementType.PHASE: "deg",
}
_MEASUREMENT_INVALID = 9.9e37  # Rigol sentinel for an unavailable measurement.

logger = logging.getLogger(__name__)


class Scope:
    """High-level control of one connected instrument."""

    def __init__(self, transport: Transport, dialect: Dialect, idn: IdnInfo) -> None:
        self._t = transport
        self._d = dialect
        self._idn = idn
        self._limits = dialect.limits()

    # -- identity / lifecycle --------------------------------------------
    def identify(self) -> IdnInfo:
        return self._idn

    @property
    def dialect_series(self) -> str:
        return self._d.series

    def reset(self) -> None:
        self._t.write(self._d.reset())
        self._t.write(self._d.clear_status())

    # -- analog channel --------------------------------------------------
    def configure_channel(self, config: ChannelConfig) -> None:
        self._validate_channel("configure_channel", config.channel)
        if config.scale_v_per_div is not None:
            self._validate_scale("configure_channel", config.scale_v_per_div)
        ch = config.channel
        if config.enabled is not None:
            self._t.write(self._d.channel_enable(ch, config.enabled))
        if config.scale_v_per_div is not None:
            self._t.write(self._d.channel_scale(ch, config.scale_v_per_div))
        if config.offset_v is not None:
            self._t.write(self._d.channel_offset(ch, config.offset_v))
        if config.coupling is not None:
            self._t.write(self._d.channel_coupling(ch, config.coupling))
        if config.bandwidth_limit is not None:
            self._t.write(self._d.channel_bandwidth(ch, config.bandwidth_limit))
        if config.probe_ratio is not None:
            self._t.write(self._d.channel_probe(ch, config.probe_ratio))
        self._check_errors("configure_channel")

    # -- digital / logic -------------------------------------------------
    def configure_logic(self, config: LogicConfig) -> None:
        if not config.channels:
            raise ValidationError(
                "configure_logic", parameter="channels", value=config.channels,
                requirement="at least one digital channel",
            )
        for d in config.channels:
            self._validate_digital("configure_logic", d)
        for d in config.channels:
            if config.enabled is not None:
                self._t.write(self._d.logic_enable(d, config.enabled))
            if config.threshold_v is not None:
                self._t.write(self._d.logic_threshold(d, config.threshold_v))
        self._check_errors("configure_logic")

    # -- trigger ---------------------------------------------------------
    def configure_trigger(self, config: TriggerConfig) -> None:
        self._t.write(self._d.trigger_mode(config.mode))
        if config.mode is TriggerMode.EDGE:
            if config.source is not None:
                self._t.write(self._d.trigger_edge_source(config.source))
            if config.slope is not None:
                self._t.write(self._d.trigger_edge_slope(config.slope))
            if config.level_v is not None:
                self._t.write(self._d.trigger_edge_level(config.level_v))
        self._check_errors("configure_trigger")

    # -- acquisition -----------------------------------------------------
    def capture(self, action: CaptureAction) -> None:
        command = {
            CaptureAction.RUN: self._d.run,
            CaptureAction.STOP: self._d.stop,
            CaptureAction.SINGLE: self._d.single,
        }[action]()
        self._t.write(command)
        self._check_errors("capture")

    # -- measurement -----------------------------------------------------
    def measure(
        self, mtype: MeasurementType, source: str, source2: str | None = None
    ) -> MeasurementResult:
        raw = self._t.query(self._d.measure_item(mtype, source, source2))
        value = float(raw)
        if value >= _MEASUREMENT_INVALID:
            raise SkippyError(
                "measure",
                reason=f"{mtype.value} on {source} is unavailable",
                check=(
                    "ensure the channel is enabled, a signal is present, "
                    "and the scope is running"
                ),
                inputs={"type": mtype.value, "source": source},
            )
        return MeasurementResult(type=mtype, source=source, value=value, unit=_UNITS[mtype])

    # -- screenshot ------------------------------------------------------
    def screenshot(self) -> Screenshot:
        """Capture the screen in the scope's native image format (no transcode)."""
        data = self._t.query_binary(self._d.screenshot())
        return Screenshot(image_format=self._d.native_screenshot_format, data=data)

    # -- waveform --------------------------------------------------------
    def read_waveform(
        self, source: str, mode: WaveformMode = WaveformMode.NORMAL, max_points: int = 1200
    ) -> WaveformData:
        if max_points <= 0:
            raise ValidationError(
                "read_waveform", parameter="max_points", value=max_points,
                requirement="greater than 0",
            )
        self._t.write(self._d.waveform_source(source))
        self._t.write(self._d.waveform_mode(mode))
        self._t.write(self._d.waveform_format_byte())
        pre = _parse_preamble(self._t.query(self._d.waveform_preamble()))
        raw = self._t.query_binary(self._d.waveform_data())
        volts = tuple(
            (byte - pre.y_origin - pre.y_reference) * pre.y_increment for byte in raw
        )
        if len(volts) > max_points:
            logger.warning(
                "read_waveform: downsampling %d samples to <=%d (max_points)",
                len(volts), max_points,
            )
            volts = _downsample(volts, max_points)
        return WaveformData(
            source=source,
            x_unit="s",
            y_unit="V",
            x_increment=pre.x_increment,
            x_origin=pre.x_origin,
            values=volts,
        )

    # -- bus decode ------------------------------------------------------
    def decode_bus(self, config: BusConfig) -> list[DecodedFrame]:
        if config.bus not in (1, 2):
            raise ValidationError(
                "decode_bus", parameter="bus", value=config.bus, requirement="1 or 2"
            )
        self._t.write(self._d.bus_protocol(config.bus, config.protocol))
        self._t.write(self._d.bus_enable(config.bus, True))
        self._check_errors("decode_bus")
        raw = self._t.query(self._d.bus_decode_data(config.bus)).strip()
        return _parse_frames(raw)

    # -- raw escape hatch ------------------------------------------------
    def raw_scpi(self, command: str, expect_response: bool | None = None) -> str | None:
        if expect_response is None:
            wants_response = command.strip().endswith("?")
        else:
            wants_response = expect_response
        if wants_response:
            return self._t.query(command)
        self._t.write(command)
        return None

    # -- internals -------------------------------------------------------
    def _validate_channel(self, operation: str, ch: int) -> None:
        if not 1 <= ch <= self._limits.num_analog_channels:
            raise ValidationError(
                operation, parameter="channel", value=ch,
                requirement=f"1 <= channel <= {self._limits.num_analog_channels}",
            )

    def _validate_digital(self, operation: str, d: int) -> None:
        if not 0 <= d <= self._limits.num_digital_channels - 1:
            raise ValidationError(
                operation, parameter="digital_channel", value=d,
                requirement=f"0 <= channel <= {self._limits.num_digital_channels - 1}",
            )

    def _validate_scale(self, operation: str, scale: float) -> None:
        lo, hi = self._limits.min_scale_v_per_div, self._limits.max_scale_v_per_div
        if not lo <= scale <= hi:
            raise ValidationError(
                operation, parameter="scale_v_per_div", value=scale,
                requirement=f"{lo} <= scale <= {hi}",
            )

    def _check_errors(self, operation: str) -> None:
        code, message = _parse_system_error(self._t.query(self._d.system_error()))
        if code != 0:
            raise ScpiError(operation, code=code, message=message)


class _Preamble:
    __slots__ = ("x_increment", "x_origin", "y_increment", "y_origin", "y_reference")

    def __init__(
        self,
        x_increment: float,
        x_origin: float,
        y_increment: float,
        y_origin: float,
        y_reference: float,
    ) -> None:
        self.x_increment = x_increment
        self.x_origin = x_origin
        self.y_increment = y_increment
        self.y_origin = y_origin
        self.y_reference = y_reference


def _parse_preamble(raw: str) -> _Preamble:
    f = [p.strip() for p in raw.split(",")]
    return _Preamble(
        x_increment=float(f[4]),
        x_origin=float(f[5]),
        y_increment=float(f[7]),
        y_origin=float(f[8]),
        y_reference=float(f[9]),
    )


def _parse_system_error(raw: str) -> tuple[int, str]:
    code_str, _, message = raw.strip().partition(",")
    return int(code_str), message.strip().strip('"')


def _parse_frames(raw: str) -> list[DecodedFrame]:
    if not raw:
        return []
    frames: list[DecodedFrame] = []
    for chunk in raw.split(";"):
        time_str, label, data = (chunk.split(",", 2) + ["", ""])[:3]
        frames.append(DecodedFrame(time=float(time_str), data=data, label=label))
    return frames


def _downsample(values: tuple[float, ...], max_points: int) -> tuple[float, ...]:
    if len(values) <= max_points:
        return values
    stride = len(values) // max_points + 1
    return values[::stride]
