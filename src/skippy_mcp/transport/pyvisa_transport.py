"""PyVISA-backed transport for real instruments.

Wraps a PyVISA message-based resource and maps VISA faults onto the actionable
:class:`SkippyError` hierarchy. Not exercised until a live instrument is on the
LAN (plan Phase 7); kept fully typed so it compiles alongside the rest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Never, cast

import pyvisa
from pyvisa.errors import VisaIOError

from skippy_mcp.core.errors import ConnectionFailedError, InstrumentTimeoutError

if TYPE_CHECKING:
    from pyvisa.resources import MessageBasedResource

_DEFAULT_BACKEND = "@py"  # pure-Python pyvisa-py; no vendor VISA install needed.


def _is_timeout(exc: VisaIOError) -> bool:
    return exc.error_code == pyvisa.constants.StatusCode.error_timeout


class PyVisaTransport:
    """A :class:`Transport` backed by a live PyVISA resource."""

    def __init__(self, resource: MessageBasedResource, *, timeout_ms: int) -> None:
        self._resource = resource
        self._timeout_ms = timeout_ms

    @classmethod
    def open(
        cls,
        resource_string: str,
        *,
        timeout_ms: int = 5000,
        backend: str = _DEFAULT_BACKEND,
    ) -> PyVisaTransport:
        """Open ``resource_string`` (e.g. ``TCPIP0::192.168.1.50::INSTR``)."""
        try:
            manager = pyvisa.ResourceManager(backend)
            resource: Any = manager.open_resource(resource_string)
        except Exception as exc:  # noqa: BLE001 - normalize any backend failure
            raise ConnectionFailedError(resource_string, reason=str(exc)) from exc
        resource.timeout = timeout_ms
        resource.read_termination = "\n"
        resource.write_termination = "\n"
        return cls(resource, timeout_ms=timeout_ms)

    def write(self, command: str) -> None:
        try:
            self._resource.write(command)
        except VisaIOError as exc:
            self._raise_for(exc, command)

    def query(self, command: str) -> str:
        try:
            return str(self._resource.query(command)).strip()
        except VisaIOError as exc:
            self._raise_for(exc, command)

    def query_binary(self, command: str) -> bytes:
        try:
            # container=bytes yields bytes at runtime though the stub says Sequence.
            return cast(
                bytes,
                self._resource.query_binary_values(command, datatype="B", container=bytes),
            )
        except VisaIOError as exc:
            self._raise_for(exc, command)

    def close(self) -> None:
        self._resource.close()

    def _raise_for(self, exc: VisaIOError, command: str) -> Never:
        if _is_timeout(exc):
            raise InstrumentTimeoutError(
                "instrument_io", command=command, timeout_ms=self._timeout_ms
            ) from exc
        raise ConnectionFailedError(command, reason=str(exc)) from exc
