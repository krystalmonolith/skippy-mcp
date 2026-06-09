"""Dialect layer: semantic operation -> SCPI string, per instrument series.

The driver calls typed dialect methods and never embeds SCPI literals. The base
class provides UltraVision-style defaults for the analog/acquisition/trigger/
measurement/waveform families; mixed-signal operations (``:LA:``/``:BUS:``) raise
:class:`NotSupportedError` unless a subclass overrides them.

Series are discovered at connect time by parsing ``*IDN?`` and asking each
registered dialect whether it :meth:`~Dialect.matches`. Unknown instruments are
refused (no silent fallback).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from skippy_mcp.core.enums import (
    BandwidthLimit,
    BusProtocol,
    Coupling,
    ImageFormat,
    MeasurementType,
    TriggerMode,
    TriggerSlope,
    WaveformMode,
)
from skippy_mcp.core.errors import NotSupportedError, UnsupportedInstrumentError
from skippy_mcp.core.models import DeviceLimits, IdnInfo


class Dialect(ABC):
    """Maps semantic operations to SCPI for one instrument series."""

    series: str = "GENERIC"
    #: Image format the scope returns from :meth:`screenshot` (transcoded later).
    native_screenshot_format: ImageFormat = ImageFormat.BMP

    @abstractmethod
    def matches(self, idn: IdnInfo) -> bool:
        """True if this dialect drives the identified instrument."""

    @abstractmethod
    def limits(self) -> DeviceLimits:
        """Per-model bounds for pre-validation."""

    # -- system ----------------------------------------------------------
    def idn(self) -> str:
        return "*IDN?"

    def reset(self) -> str:
        return "*RST"

    def clear_status(self) -> str:
        return "*CLS"

    def system_error(self) -> str:
        return ":SYSTem:ERRor?"

    # -- source naming ---------------------------------------------------
    def scpi_source(self, name: str) -> str:
        """Translate an API source token (``CH1``..``CH4``, ``D0``..``D15``)."""
        upper = name.upper()
        if upper.startswith("CH") and upper[2:].isdigit():
            return f"CHANnel{upper[2:]}"
        if upper.startswith("D") and upper[1:].isdigit():
            return upper
        return name

    # -- analog channel --------------------------------------------------
    def channel_enable(self, ch: int, on: bool) -> str:
        return f":CHANnel{ch}:DISPlay {'ON' if on else 'OFF'}"

    def channel_scale(self, ch: int, volts_per_div: float) -> str:
        return f":CHANnel{ch}:SCALe {volts_per_div:g}"

    def query_channel_scale(self, ch: int) -> str:
        return f":CHANnel{ch}:SCALe?"

    def channel_offset(self, ch: int, offset_v: float) -> str:
        return f":CHANnel{ch}:OFFSet {offset_v:g}"

    def channel_coupling(self, ch: int, coupling: Coupling) -> str:
        return f":CHANnel{ch}:COUPling {coupling.scpi}"

    def channel_bandwidth(self, ch: int, limit: BandwidthLimit) -> str:
        return f":CHANnel{ch}:BWLimit {limit.scpi}"

    def channel_probe(self, ch: int, ratio: float) -> str:
        return f":CHANnel{ch}:PROBe {ratio:g}"

    # -- digital / logic (MSO only) --------------------------------------
    def logic_enable(self, d: int, on: bool) -> str:
        raise NotSupportedError("configure_logic", series=self.series, reason="no logic channels")

    def logic_threshold(self, d: int, threshold_v: float) -> str:
        raise NotSupportedError("configure_logic", series=self.series, reason="no logic channels")

    # -- acquisition -----------------------------------------------------
    def run(self) -> str:
        return ":RUN"

    def stop(self) -> str:
        return ":STOP"

    def single(self) -> str:
        return ":SINGle"

    # -- trigger (edge) --------------------------------------------------
    def trigger_mode(self, mode: TriggerMode) -> str:
        return f":TRIGger:MODE {mode.scpi}"

    def trigger_edge_source(self, source: str) -> str:
        return f":TRIGger:EDGE:SOURce {self.scpi_source(source)}"

    def trigger_edge_slope(self, slope: TriggerSlope) -> str:
        return f":TRIGger:EDGE:SLOPe {slope.scpi}"

    def trigger_edge_level(self, level_v: float) -> str:
        return f":TRIGger:EDGE:LEVel {level_v:g}"

    # -- measurement -----------------------------------------------------
    def measure_item(self, mtype: MeasurementType, source: str, source2: str | None = None) -> str:
        srcs = self.scpi_source(source)
        if source2 is not None:
            srcs += f",{self.scpi_source(source2)}"
        return f":MEASure:ITEM? {mtype.scpi},{srcs}"

    # -- screenshot ------------------------------------------------------
    @abstractmethod
    def screenshot(self) -> str:
        """Command that returns a screen image (in ``native_screenshot_format``)."""

    # -- waveform --------------------------------------------------------
    def waveform_source(self, source: str) -> str:
        return f":WAVeform:SOURce {self.scpi_source(source)}"

    def waveform_mode(self, mode: WaveformMode) -> str:
        return f":WAVeform:MODE {mode.scpi}"

    def waveform_format_byte(self) -> str:
        return ":WAVeform:FORMat BYTE"

    def waveform_preamble(self) -> str:
        return ":WAVeform:PREamble?"

    def waveform_data(self) -> str:
        return ":WAVeform:DATA?"

    # -- bus decode (MSO only) -------------------------------------------
    def bus_protocol(self, bus: int, protocol: BusProtocol) -> str:
        raise NotSupportedError("decode_bus", series=self.series, reason="no bus decode")

    def bus_enable(self, bus: int, on: bool) -> str:
        raise NotSupportedError("decode_bus", series=self.series, reason="no bus decode")

    def bus_decode_data(self, bus: int) -> str:
        raise NotSupportedError("decode_bus", series=self.series, reason="no bus decode")


_REGISTRY: list[type[Dialect]] = []


def register(cls: type[Dialect]) -> type[Dialect]:
    """Class decorator: add a concrete dialect to the selection registry."""
    _REGISTRY.append(cls)
    return cls


def supported_series() -> list[str]:
    """Series strings of all registered dialects."""
    return [cls.series for cls in _REGISTRY]


def select_dialect(idn: IdnInfo) -> Dialect:
    """Return a dialect for ``idn``, or refuse with an actionable error."""
    for cls in _REGISTRY:
        candidate = cls()
        if candidate.matches(idn):
            return candidate
    raise UnsupportedInstrumentError(idn, supported_series())
