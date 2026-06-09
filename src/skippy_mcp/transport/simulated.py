"""In-memory simulation of an MSO5000-series instrument.

``SimulatedTransport`` answers the SCPI that :class:`MSO5000Dialect` emits, so the
whole stack above the transport can be built and tested with no hardware. It is a
first-class deliverable, not test scaffolding: it also underpins an offline demo
mode.

Behavior is intentionally deterministic (no randomness) so tests are stable.
"""

from __future__ import annotations

import math

# A minimal but valid PNG (1x1) — enough for screenshot round-trip assertions.
_FAKE_PNG: bytes = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

# Deterministic measurement values keyed by SCPI item mnemonic.
_MEASUREMENTS: dict[str, str] = {
    "VPP": "3.300000E+00",
    "VRMS": "1.166726E+00",
    "FREQuency": "1.000000E+03",
    "PERiod": "1.000000E-03",
    "PDUTy": "5.000000E+01",
    "RTIMe": "3.500000E-06",
    "FTIMe": "3.500000E-06",
    "RDELay": "0.000000E+00",
    "RPHase": "0.000000E+00",
}
_MEASUREMENT_INVALID = "9.9E37"  # Rigol sentinel for an unavailable measurement.

_IDN = "RIGOL TECHNOLOGIES,MSO5204,SIM00000000,00.01.03.02.02"
_WAVEFORM_POINTS = 64


class SimulatedTransport:
    """A fake MSO5204 that satisfies the :class:`Transport` protocol."""

    def __init__(self) -> None:
        self._state: dict[str, str] = {}
        self._errors: list[tuple[int, str]] = []
        self.measurement_override: str | None = None
        self.history: list[str] = []
        self.closed = False

    # -- test helpers -----------------------------------------------------
    def queue_error(self, code: int, message: str) -> None:
        """Make the next ``:SYSTem:ERRor?`` report this error (for tests)."""
        self._errors.append((code, message))

    def force_measurement(self, value: str) -> None:
        """Make every measurement query return ``value`` (for tests)."""
        self.measurement_override = value

    # -- Transport protocol ----------------------------------------------
    def write(self, command: str) -> None:
        self.history.append(command)
        cmd = command.strip()
        if cmd in ("*RST",):
            self._state.clear()
            self._errors.clear()
            return
        if cmd in ("*CLS",):
            self._errors.clear()
            return
        node, _, arg = cmd.partition(" ")
        if arg:
            self._state[node.upper()] = arg.strip()

    def query(self, command: str) -> str:
        self.history.append(command)
        cmd = command.strip()
        if cmd == "*IDN?":
            return _IDN
        if cmd in (":SYSTem:ERRor?", ":SYST:ERR?"):
            code, message = self._errors.pop(0) if self._errors else (0, "No error")
            return f'{code},"{message}"'
        if cmd.startswith(":MEASure:ITEM?") or cmd.startswith(":MEAS:ITEM?"):
            return self._measure(cmd)
        if cmd.startswith(":WAVeform:PREamble?") or cmd.startswith(":WAV:PRE?"):
            return self._waveform_preamble()
        if cmd.startswith(":BUS") and cmd.endswith(":DATA?"):
            # Canned decode: frames are "time,label,data" separated by ';'.
            return "0.000000E+00,I2C,0x3A;1.000000E-04,I2C,0x42"
        # Generic set/query round-trip.
        node = cmd[:-1].upper() if cmd.endswith("?") else cmd.upper()
        return self._normalize(node, self._state.get(node, "0"))

    def query_binary(self, command: str) -> bytes:
        self.history.append(command)
        cmd = command.strip()
        if cmd.startswith(":DISPlay:DATA?") or cmd.startswith(":DISP:DATA?"):
            return _FAKE_PNG
        if cmd.startswith(":WAVeform:DATA?") or cmd.startswith(":WAV:DATA?"):
            return self._waveform_bytes()
        return b""

    def close(self) -> None:
        self.closed = True

    # -- internals --------------------------------------------------------
    def _measure(self, cmd: str) -> str:
        if self.measurement_override is not None:
            return self.measurement_override
        _, _, arg = cmd.partition("?")
        item = arg.strip().split(",", 1)[0].strip()
        return _MEASUREMENTS.get(item, _MEASUREMENT_INVALID)

    @staticmethod
    def _normalize(node: str, value: str) -> str:
        """Echo stored state, normalizing on/off words for DISPlay-style nodes."""
        if node.endswith(("DISPLAY", ":DISP")):
            return "1" if value.upper() in ("ON", "1", "TRUE") else "0"
        return value

    @staticmethod
    def _waveform_bytes() -> bytes:
        return bytes(
            128 + int(120 * math.sin(2 * math.pi * i / _WAVEFORM_POINTS))
            for i in range(_WAVEFORM_POINTS)
        )

    @staticmethod
    def _waveform_preamble() -> str:
        # format,type,points,count,xincrement,xorigin,xreference,yincrement,yorigin,yreference
        return (
            f"0,0,{_WAVEFORM_POINTS},1,"
            "1.000000E-06,-3.200000E-05,0,"
            "4.000000E-02,128,0"
        )
