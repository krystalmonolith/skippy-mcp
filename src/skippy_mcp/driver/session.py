"""Connection lifecycle: open transport, identify, select dialect, build Scope."""

from __future__ import annotations

from skippy_mcp.config import ServerConfig
from skippy_mcp.core.models import IdnInfo
from skippy_mcp.dialect import select_dialect
from skippy_mcp.driver.scope import Scope
from skippy_mcp.transport.base import Transport
from skippy_mcp.transport.pyvisa_transport import PyVisaTransport


def open_transport(config: ServerConfig) -> Transport:
    """Open the real PyVISA transport described by ``config``."""
    return PyVisaTransport.open(config.resource, timeout_ms=config.timeout_ms)


def establish(
    transport: Transport,
    *,
    reset_on_connect: bool = True,
    default_timeout_ms: int | None = None,
) -> Scope:
    """Identify the instrument on ``transport``, select a dialect, return a Scope.

    ``*IDN?`` is an IEEE-488.2 universal query, so it is issued before a dialect
    is known. Unknown models are refused by :func:`select_dialect`. ``default_timeout_ms``
    is the per-I/O timeout restored after a per-call override (see :meth:`Scope.io_timeout`).
    """
    idn = IdnInfo.parse(transport.query("*IDN?"))
    dialect = select_dialect(idn)
    scope = Scope(transport, dialect, idn, default_timeout_ms=default_timeout_ms)
    if reset_on_connect:
        scope.reset()
    return scope
