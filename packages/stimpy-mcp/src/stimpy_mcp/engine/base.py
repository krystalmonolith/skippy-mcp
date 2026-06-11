"""Engine abstraction.

Everything above this layer depends only on :class:`StimulusEngine`, never on
pigpio. That keeps the driver and MCP layers testable against the in-memory
:class:`~stimpy_mcp.engine.simulated.SimulatedEngine` with no Pi and no daemon.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from stimpy_mcp.core.enums import RunMode
from stimpy_mcp.core.models import EngineLimits, EngineStatus, Pattern, PinMap, StagedHandle


@runtime_checkable
class StimulusEngine(Protocol):
    """Drives a parallel digital pattern out of GPIO lines, double-buffered."""

    def configure(self, pin_map: PinMap) -> None:
        """Claim the data + sync lines as outputs, all low. Idempotent."""
        ...

    def stage_pattern(self, pattern: Pattern) -> StagedHandle:
        """Build the device-side buffer (pigpio wave[s]) WITHOUT going live.

        Validates the pattern against :meth:`limits`; raises
        :class:`~stimpy_mcp.core.errors.BufferTooLargeError` if it will not fit.
        """
        ...

    def go_live(self, handle: StagedHandle, *, mode: RunMode) -> None:
        """Activate a staged buffer at the next loop boundary (the SYNC frame).

        With a pattern already running in REPEAT mode, the swap is glitch-free:
        the device finishes the current cycle, then begins ``handle`` at frame 0.
        """
        ...

    def stop(self) -> None:
        """Halt transmission and drive all lines low."""
        ...

    def status(self) -> EngineStatus:
        """A consistent snapshot of run state, frame counts, and clock."""
        ...

    def limits(self) -> EngineLimits:
        """Device limits (max pulses/CBs, min tick, max frames per wave)."""
        ...

    def close(self) -> None:
        """Release lines and the device connection."""
        ...
