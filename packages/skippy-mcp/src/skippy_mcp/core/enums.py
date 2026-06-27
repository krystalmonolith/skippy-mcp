"""Domain enumerations.

Each enum is a string enum whose member ``value`` is the public API token (the
lowercase string used in MCP tool schemas) and whose ``scpi`` property is the
SCPI mnemonic sent to the instrument. Keeping both on one member means the
mapping lives in exactly one place.
"""

from __future__ import annotations

from enum import StrEnum


class ScpiEnum(StrEnum):
    """String enum carrying both an API token (``value``) and a SCPI token."""

    _scpi_token: str

    def __new__(cls, api: str, scpi: str) -> ScpiEnum:
        obj = str.__new__(cls, api)
        obj._value_ = api
        obj._scpi_token = scpi
        return obj

    @property
    def scpi(self) -> str:
        """The SCPI mnemonic for this member."""
        return self._scpi_token


class Coupling(ScpiEnum):
    """Analog channel input coupling."""

    DC = ("dc", "DC")
    AC = ("ac", "AC")
    GND = ("gnd", "GND")


class BandwidthLimit(ScpiEnum):
    """Analog channel bandwidth limit."""

    OFF = ("off", "OFF")
    MHZ_20 = ("20m", "20M")


class TriggerMode(ScpiEnum):
    """Trigger mode."""

    EDGE = ("edge", "EDGE")
    PULSE = ("pulse", "PULSe")
    PATTERN = ("pattern", "PATTern")


class TriggerSlope(ScpiEnum):
    """Edge-trigger slope."""

    RISING = ("rising", "POSitive")
    FALLING = ("falling", "NEGative")
    EITHER = ("either", "RFALl")


class MeasurementType(ScpiEnum):
    """Automated measurement item."""

    VPP = ("vpp", "VPP")
    VRMS = ("vrms", "VRMS")
    FREQ = ("freq", "FREQuency")
    PERIOD = ("period", "PERiod")
    DUTY = ("duty", "PDUTy")
    RISE = ("rise", "RTIMe")
    FALL = ("fall", "FTIMe")
    DELAY = ("delay", "RDELay")
    PHASE = ("phase", "RPHase")


class BusProtocol(ScpiEnum):
    """Serial/parallel bus decode protocol."""

    I2C = ("i2c", "IIC")
    SPI = ("spi", "SPI")
    UART = ("uart", "RS232")
    PARALLEL = ("parallel", "PARallel")
    CAN = ("can", "CAN")
    LIN = ("lin", "LIN")


class I2cAddressMode(ScpiEnum):
    """How an I2C decode reports the address byte.

    ``RW`` folds the read/write bit into the reported address; ``NORMAL`` reports
    the 7/10-bit address only. SCPI tokens verified on an MSO5204.
    """

    NORMAL = ("normal", "NORMal")
    RW = ("rw", "RW")


class BusFormat(ScpiEnum):
    """Radix the bus decoder uses for displayed/returned data.

    SCPI tokens verified on an MSO5204 — note the scope rejects the spelled-out
    ``DECimal``/``BINary`` forms, so the short tokens are the correct ones.
    """

    HEX = ("hex", "HEX")
    DEC = ("dec", "DEC")
    BIN = ("bin", "BIN")
    ASCII = ("ascii", "ASCii")


class ImageFormat(ScpiEnum):
    """Screenshot image format."""

    PNG = ("png", "PNG")
    BMP = ("bmp", "BMP")


class CaptureAction(ScpiEnum):
    """Acquisition control action."""

    RUN = ("run", "RUN")
    STOP = ("stop", "STOP")
    SINGLE = ("single", "SINGle")


class WaveformMode(ScpiEnum):
    """Waveform read mode."""

    NORMAL = ("normal", "NORMal")
    RAW = ("raw", "RAW")
    MAX = ("max", "MAXimum")
