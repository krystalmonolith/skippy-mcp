"""Transport abstraction.

Everything above this layer depends only on :class:`Transport`, never on PyVISA.
That keeps the driver, dialect, and MCP layers testable against the in-memory
:class:`~skippy_mcp.transport.simulated.SimulatedTransport` with no hardware.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Transport(Protocol):
    """A byte/line oriented channel to a SCPI instrument."""

    def write(self, command: str) -> None:
        """Send a command that expects no response."""
        ...

    def query(self, command: str) -> str:
        """Send a query and return its text response (without termination)."""
        ...

    def query_binary(self, command: str) -> bytes:
        """Send a query and return its IEEE-488.2 binary-block payload."""
        ...

    def close(self) -> None:
        """Release the underlying resource."""
        ...
