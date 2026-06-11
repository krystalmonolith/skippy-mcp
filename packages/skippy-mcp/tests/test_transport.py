"""Phase 2 tests: transport layer — Protocol conformance and the simulator."""

from __future__ import annotations

import pytest

from skippy_mcp.transport.base import Transport
from skippy_mcp.transport.simulated import SimulatedTransport


def test_simulated_satisfies_transport_protocol() -> None:
    sim = SimulatedTransport()
    assert isinstance(sim, Transport)


def test_idn_identifies_as_mso5204() -> None:
    sim = SimulatedTransport()
    assert "MSO5204" in sim.query("*IDN?")


def test_set_then_query_round_trips() -> None:
    sim = SimulatedTransport()
    sim.write(":CHANnel1:SCALe 0.1")
    assert sim.query(":CHANnel1:SCALe?") == "0.1"


def test_display_node_normalizes_to_boolean() -> None:
    sim = SimulatedTransport()
    sim.write(":CHANnel1:DISPlay ON")
    assert sim.query(":CHANnel1:DISPlay?") == "1"
    sim.write(":CHANnel1:DISPlay OFF")
    assert sim.query(":CHANnel1:DISPlay?") == "0"


def test_error_queue_default_is_no_error() -> None:
    sim = SimulatedTransport()
    assert sim.query(":SYSTem:ERRor?") == '0,"No error"'


def test_queued_error_is_reported_then_cleared() -> None:
    sim = SimulatedTransport()
    sim.queue_error(-222, "Data out of range")
    assert sim.query(":SYSTem:ERRor?") == '-222,"Data out of range"'
    assert sim.query(":SYSTem:ERRor?") == '0,"No error"'


def test_measurement_returns_deterministic_value() -> None:
    sim = SimulatedTransport()
    assert sim.query(":MEASure:ITEM? VPP,CHANnel1") == "3.300000E+00"


def test_unknown_measurement_returns_sentinel() -> None:
    sim = SimulatedTransport()
    assert sim.query(":MEASure:ITEM? NOPE,CHANnel1") == "9.9E37"


def test_screenshot_returns_bmp_signature() -> None:
    sim = SimulatedTransport()
    data = sim.query_binary(":DISPlay:DATA?")
    assert data.startswith(b"BM")  # MSO5000 native format is 24-bit BMP


def test_waveform_bytes_and_preamble() -> None:
    sim = SimulatedTransport()
    wave = sim.query_binary(":WAVeform:DATA?")
    assert len(wave) == 64
    preamble = sim.query(":WAVeform:PREamble?")
    assert preamble.split(",")[2] == "64"  # points field


def test_reset_clears_state_and_errors() -> None:
    sim = SimulatedTransport()
    sim.write(":CHANnel1:SCALe 0.5")
    sim.queue_error(-100, "Command error")
    sim.write("*RST")
    assert sim.query(":CHANnel1:SCALe?") == "0"  # back to default
    assert sim.query(":SYSTem:ERRor?") == '0,"No error"'


def test_history_records_commands() -> None:
    sim = SimulatedTransport()
    sim.write(":RUN")
    sim.query("*IDN?")
    assert ":RUN" in sim.history
    assert "*IDN?" in sim.history


def test_close_marks_closed() -> None:
    sim = SimulatedTransport()
    sim.close()
    assert sim.closed


def test_socket_error_becomes_actionable_connect_error() -> None:
    """A raw socket failure (scope off) surfaces as an actionable ConnectionFailedError,
    not an uncaught ConnectionRefusedError traceback."""
    from skippy_mcp.core.errors import ConnectionFailedError
    from skippy_mcp.transport.pyvisa_transport import PyVisaTransport

    class _DeadResource:
        resource_name = "TCPIP0::127.0.0.1::5555::SOCKET"

        def query(self, command: str) -> str:
            raise ConnectionRefusedError(111, "Connection refused")

    transport = PyVisaTransport(_DeadResource(), timeout_ms=3000)
    with pytest.raises(ConnectionFailedError) as excinfo:
        transport.query("*IDN?")
    message = str(excinfo.value)
    assert message.startswith("connect:")
    assert "Check:" in message
    assert "Connection refused" in message
