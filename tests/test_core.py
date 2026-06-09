"""Phase 1 tests: core domain — enums, models, errors."""

from __future__ import annotations

import dataclasses

import pytest

from skippy_mcp.core.enums import Coupling, MeasurementType, TriggerSlope
from skippy_mcp.core.errors import (
    InstrumentTimeoutError,
    SkippyError,
    UnsupportedInstrumentError,
    ValidationError,
)
from skippy_mcp.core.models import IdnInfo


def test_scpi_enum_carries_both_tokens() -> None:
    assert Coupling.DC.value == "dc"
    assert Coupling.DC.scpi == "DC"
    assert Coupling("dc") is Coupling.DC  # API token round-trips
    assert TriggerSlope.RISING.scpi == "POSitive"
    assert MeasurementType.FREQ.scpi == "FREQuency"


def test_scpi_enum_is_a_str() -> None:
    assert isinstance(Coupling.AC, str)
    assert Coupling.AC == "ac"


def test_idn_parse_full() -> None:
    idn = IdnInfo.parse("RIGOL TECHNOLOGIES,MSO5204,MS5A000,00.01.03")
    assert idn.manufacturer == "RIGOL TECHNOLOGIES"
    assert idn.model == "MSO5204"
    assert idn.serial == "MS5A000"
    assert idn.firmware == "00.01.03"


def test_idn_parse_missing_fields_are_empty() -> None:
    idn = IdnInfo.parse("RIGOL,MSO5204")
    assert idn.serial == ""
    assert idn.firmware == ""


def test_models_are_frozen() -> None:
    idn = IdnInfo.parse("RIGOL,MSO5204,S,F")
    with pytest.raises(dataclasses.FrozenInstanceError):
        idn.model = "other"  # type: ignore[misc]


def test_error_message_contract_has_operation_input_reason_check() -> None:
    err = ValidationError(
        "configure_channel", parameter="channel", value=9, requirement="1 <= channel <= 4"
    )
    msg = str(err)
    assert msg.startswith("configure_channel:")
    assert "Input:" in msg and "channel=9" in msg
    assert "Check:" in msg and "1 <= channel <= 4" in msg
    assert isinstance(err, SkippyError)


def test_timeout_error_includes_command_and_timeout() -> None:
    err = InstrumentTimeoutError("measure", command=":MEAS:VPP?", timeout_ms=5000)
    msg = str(err)
    assert ":MEAS:VPP?" in msg
    assert "5000 ms" in msg


def test_unsupported_instrument_lists_supported_series() -> None:
    idn = IdnInfo.parse("ACME,XYZ123,S,F")
    err = UnsupportedInstrumentError(idn, ["MSO5000", "DHO800"])
    msg = str(err)
    assert "XYZ123" in msg
    assert "MSO5000" in msg and "DHO800" in msg
