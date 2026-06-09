"""Phase 4 tests: driver + config, exercised against the simulator."""

from __future__ import annotations

import pytest

from skippy_mcp.core.enums import (
    BusProtocol,
    CaptureAction,
    Coupling,
    ImageFormat,
    MeasurementType,
    TriggerMode,
)
from skippy_mcp.core.errors import ScpiError, SkippyError, ValidationError
from skippy_mcp.core.models import BusConfig, ChannelConfig, LogicConfig, TriggerConfig
from skippy_mcp.driver.scope import Scope
from skippy_mcp.driver.session import establish
from skippy_mcp.transport.simulated import SimulatedTransport


@pytest.fixture
def scope() -> Scope:
    return establish(SimulatedTransport(), reset_on_connect=True)


# -- session / identity ---------------------------------------------------
def test_establish_identifies_and_selects_dialect(scope: Scope) -> None:
    assert scope.identify().model == "MSO5204"
    assert scope.dialect_series == "MSO5000"


def test_reset_on_connect_issues_rst() -> None:
    sim = SimulatedTransport()
    establish(sim, reset_on_connect=True)
    assert "*RST" in sim.history


def test_no_reset_skips_rst() -> None:
    sim = SimulatedTransport()
    establish(sim, reset_on_connect=False)
    assert "*RST" not in sim.history


# -- channel --------------------------------------------------------------
def test_configure_channel_emits_expected_scpi() -> None:
    sim = SimulatedTransport()
    scope = establish(sim, reset_on_connect=False)
    scope.configure_channel(
        ChannelConfig(channel=1, enabled=True, scale_v_per_div=0.1, coupling=Coupling.DC)
    )
    assert ":CHANnel1:DISPlay ON" in sim.history
    assert ":CHANnel1:SCALe 0.1" in sim.history
    assert ":CHANnel1:COUPling DC" in sim.history


def test_configure_channel_rejects_bad_channel(scope: Scope) -> None:
    with pytest.raises(ValidationError):
        scope.configure_channel(ChannelConfig(channel=9, enabled=True))


def test_configure_channel_rejects_out_of_range_scale(scope: Scope) -> None:
    with pytest.raises(ValidationError):
        scope.configure_channel(ChannelConfig(channel=1, scale_v_per_div=999.0))


# -- logic ----------------------------------------------------------------
def test_configure_logic(scope: Scope) -> None:
    # Should not raise; emits per-channel commands.
    scope.configure_logic(LogicConfig(channels=(0, 8), enabled=True, threshold_v=1.4))


def test_configure_logic_rejects_bad_digital(scope: Scope) -> None:
    with pytest.raises(ValidationError):
        scope.configure_logic(LogicConfig(channels=(16,), enabled=True))


def test_configure_logic_requires_channels(scope: Scope) -> None:
    with pytest.raises(ValidationError):
        scope.configure_logic(LogicConfig(channels=()))


# -- trigger / capture ----------------------------------------------------
def test_configure_trigger_edge(scope: Scope) -> None:
    scope.configure_trigger(TriggerConfig(mode=TriggerMode.EDGE, source="CH1", level_v=1.0))


def test_capture_actions(scope: Scope) -> None:
    for action in (CaptureAction.RUN, CaptureAction.SINGLE, CaptureAction.STOP):
        scope.capture(action)


# -- measure --------------------------------------------------------------
def test_measure_returns_value_and_unit(scope: Scope) -> None:
    result = scope.measure(MeasurementType.VPP, "CH1")
    assert result.value == pytest.approx(3.3)
    assert result.unit == "V"


def test_measure_unavailable_raises_actionable_error() -> None:
    sim = SimulatedTransport()
    scope = establish(sim, reset_on_connect=False)
    sim.force_measurement("9.9E37")  # scope reports the measurement as unavailable
    with pytest.raises(SkippyError) as exc:
        scope.measure(MeasurementType.VPP, "CH1")
    assert "unavailable" in str(exc.value)


# -- screenshot / waveform ------------------------------------------------
def test_screenshot_returns_native_bmp(scope: Scope) -> None:
    shot = scope.screenshot()
    assert shot.image_format is ImageFormat.BMP  # driver returns the native format
    assert shot.data.startswith(b"BM")


def test_read_waveform_decodes_volts(scope: Scope) -> None:
    wave = scope.read_waveform("CH1")
    assert wave.x_unit == "s"
    assert wave.y_unit == "V"
    assert len(wave.values) == 64
    assert wave.x_increment == pytest.approx(1e-6)


# -- bus decode -----------------------------------------------------------
def test_decode_bus_parses_frames(scope: Scope) -> None:
    frames = scope.decode_bus(BusConfig(bus=1, protocol=BusProtocol.I2C))
    assert len(frames) == 2
    assert frames[0].label == "I2C"
    assert frames[0].data == "0x3A"


def test_decode_bus_rejects_bad_bus(scope: Scope) -> None:
    with pytest.raises(ValidationError):
        scope.decode_bus(BusConfig(bus=9, protocol=BusProtocol.I2C))


# -- error checking -------------------------------------------------------
def test_scope_reported_error_raises_scpi_error() -> None:
    sim = SimulatedTransport()
    scope = establish(sim, reset_on_connect=False)
    sim.queue_error(-222, "Data out of range")
    with pytest.raises(ScpiError) as exc:
        scope.capture(CaptureAction.RUN)
    assert exc.value.code == -222


# -- raw escape hatch -----------------------------------------------------
def test_raw_scpi_query_and_write(scope: Scope) -> None:
    assert scope.raw_scpi("*IDN?") is not None
    assert scope.raw_scpi(":RUN") is None
