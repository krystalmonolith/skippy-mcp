"""Dialect for the Rigol MSO5000 / DS5000 series (UltraVision II)."""

from __future__ import annotations

from skippy_mcp.core.enums import BusProtocol
from skippy_mcp.core.models import DeviceLimits, IdnInfo
from skippy_mcp.dialect.base import Dialect, register


@register
class MSO5000Dialect(Dialect):
    """Drives MSO5000/DS5000 scopes: 4 analog + 16 digital channels, bus decode."""

    series = "MSO5000"

    def matches(self, idn: IdnInfo) -> bool:
        model = idn.model.upper()
        return model.startswith(("MSO5", "DS5"))

    def limits(self) -> DeviceLimits:
        return DeviceLimits(
            num_analog_channels=4,
            num_digital_channels=16,
            min_scale_v_per_div=5.0e-4,
            max_scale_v_per_div=10.0,
        )

    # -- screenshot ------------------------------------------------------
    def screenshot(self) -> str:
        # MSO5000 :DISPlay:DATA? always returns a 24-bit BMP; format args are
        # silently ignored (verified on an MSO5204). Transcoding happens later.
        return ":DISPlay:DATA?"

    # -- digital / logic -------------------------------------------------
    def logic_enable(self, d: int, on: bool) -> str:
        return f":LA:DIGital{d}:DISPlay {'ON' if on else 'OFF'}"

    def logic_threshold(self, d: int, threshold_v: float) -> str:
        # MSO5000 sets threshold per pod (D0-D7 -> POD1, D8-D15 -> POD2).
        pod = 1 if d < 8 else 2
        return f":LA:POD{pod}:THReshold {threshold_v:g}"

    # -- bus decode ------------------------------------------------------
    def bus_protocol(self, bus: int, protocol: BusProtocol) -> str:
        return f":BUS{bus}:MODE {protocol.scpi}"

    def bus_enable(self, bus: int, on: bool) -> str:
        return f":BUS{bus}:DISPlay {'ON' if on else 'OFF'}"

    def bus_decode_data(self, bus: int) -> str:
        return f":BUS{bus}:DATA?"
