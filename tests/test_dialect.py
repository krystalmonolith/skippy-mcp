"""Phase 3 tests: dialect layer — golden SCPI, selection, refusal, simulator fit."""

from __future__ import annotations

import pytest

from skippy_mcp.core.enums import (
    BandwidthLimit,
    BusProtocol,
    Coupling,
    ImageFormat,
    MeasurementType,
    TriggerMode,
    TriggerSlope,
)
from skippy_mcp.core.errors import NotSupportedError, UnsupportedInstrumentError
from skippy_mcp.core.models import IdnInfo
from skippy_mcp.dialect import MSO5000Dialect, select_dialect, supported_series
from skippy_mcp.dialect.base import Dialect


@pytest.fixture
def mso() -> MSO5000Dialect:
    return MSO5000Dialect()


def test_select_dialect_picks_mso5000_for_mso5204() -> None:
    idn = IdnInfo.parse("RIGOL TECHNOLOGIES,MSO5204,S,F")
    dialect = select_dialect(idn)
    assert isinstance(dialect, MSO5000Dialect)
    assert dialect.series == "MSO5000"


def test_select_dialect_matches_ds5000_too() -> None:
    idn = IdnInfo.parse("RIGOL,DS5104,S,F")
    assert isinstance(select_dialect(idn), MSO5000Dialect)


def test_unknown_model_is_refused() -> None:
    idn = IdnInfo.parse("ACME,XYZ999,S,F")
    with pytest.raises(UnsupportedInstrumentError) as exc:
        select_dialect(idn)
    assert "MSO5000" in str(exc.value)


def test_supported_series_includes_mso5000() -> None:
    assert "MSO5000" in supported_series()


def test_golden_analog_commands(mso: MSO5000Dialect) -> None:
    assert mso.channel_enable(1, True) == ":CHANnel1:DISPlay ON"
    assert mso.channel_scale(2, 0.1) == ":CHANnel2:SCALe 0.1"
    assert mso.query_channel_scale(3) == ":CHANnel3:SCALe?"
    assert mso.channel_offset(1, -0.5) == ":CHANnel1:OFFSet -0.5"
    assert mso.channel_coupling(1, Coupling.AC) == ":CHANnel1:COUPling AC"
    assert mso.channel_bandwidth(1, BandwidthLimit.MHZ_20) == ":CHANnel1:BWLimit 20M"
    assert mso.channel_probe(4, 10) == ":CHANnel4:PROBe 10"


def test_golden_acquisition_and_trigger(mso: MSO5000Dialect) -> None:
    assert mso.run() == ":RUN"
    assert mso.single() == ":SINGle"
    assert mso.trigger_mode(TriggerMode.EDGE) == ":TRIGger:MODE EDGE"
    assert mso.trigger_edge_source("CH1") == ":TRIGger:EDGE:SOURce CHANnel1"
    assert mso.trigger_edge_slope(TriggerSlope.RISING) == ":TRIGger:EDGE:SLOPe POSitive"
    assert mso.trigger_edge_level(1.5) == ":TRIGger:EDGE:LEVel 1.5"


def test_golden_measure_and_screenshot(mso: MSO5000Dialect) -> None:
    assert mso.measure_item(MeasurementType.VPP, "CH1") == ":MEASure:ITEM? VPP,CHANnel1"
    assert (
        mso.measure_item(MeasurementType.DELAY, "CH1", "CH2")
        == ":MEASure:ITEM? RDELay,CHANnel1,CHANnel2"
    )
    assert mso.screenshot() == ":DISPlay:DATA?"
    assert mso.native_screenshot_format is ImageFormat.BMP


def test_source_token_translation(mso: MSO5000Dialect) -> None:
    assert mso.scpi_source("CH3") == "CHANnel3"
    assert mso.scpi_source("D7") == "D7"


def test_logic_and_bus_supported_on_mso(mso: MSO5000Dialect) -> None:
    assert mso.logic_enable(5, True) == ":LA:DIGital5:DISPlay ON"
    assert mso.logic_threshold(3, 1.4) == ":LA:POD1:THReshold 1.4"
    assert mso.logic_threshold(10, 1.4) == ":LA:POD2:THReshold 1.4"
    assert mso.bus_protocol(1, BusProtocol.I2C) == ":BUS1:MODE IIC"


def test_base_refuses_logic_and_bus() -> None:
    class BareDialect(Dialect):
        series = "BARE"

        def matches(self, idn: IdnInfo) -> bool:
            return False

        def limits(self):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def screenshot(self) -> str:
            return ""

    bare = BareDialect()
    with pytest.raises(NotSupportedError):
        bare.logic_enable(0, True)
    with pytest.raises(NotSupportedError):
        bare.bus_protocol(1, BusProtocol.SPI)
