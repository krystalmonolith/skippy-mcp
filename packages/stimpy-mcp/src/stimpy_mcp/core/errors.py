"""Error model.

Every user-facing failure is a :class:`StimpyError`. Its string form follows the
actionable-message contract (copied from SkippyMCP): it names the **operation**,
the **input** that provoked it, the **reason** it failed, and what to **check** or
do next. No bare stack traces and no "X failed" reach the user.
"""

from __future__ import annotations

from collections.abc import Mapping


class StimpyError(Exception):
    """Base for all StimpyMCP errors; ``str(self)`` is an actionable message."""

    def __init__(
        self,
        operation: str,
        *,
        reason: str,
        check: str,
        inputs: Mapping[str, object] | None = None,
    ) -> None:
        self.operation = operation
        self.reason = reason
        self.check = check
        self.inputs: dict[str, object] = dict(inputs or {})
        super().__init__(self._format())

    def _format(self) -> str:
        lines = [f"{self.operation}: {self.reason}"]
        if self.inputs:
            rendered = ", ".join(f"{k}={v!r}" for k, v in self.inputs.items())
            lines.append(f"  Input:  {rendered}")
        lines.append(f"  Check:  {self.check}")
        return "\n".join(lines)


class ConfigError(StimpyError):
    """Server configuration is invalid (bad JSON, missing TLS file, etc.)."""


class ValidationError(StimpyError):
    """A tool input failed pre-validation before it reached the engine."""

    def __init__(self, operation: str, *, parameter: str, value: object, requirement: str) -> None:
        super().__init__(
            operation,
            reason=f"parameter {parameter!r} is out of range or invalid",
            check=f"{parameter} must satisfy: {requirement}",
            inputs={parameter: value},
        )


class EngineUnavailableError(StimpyError):
    """The GPIO engine could not open the kernel GPIO character device (lgpio)."""

    def __init__(self, *, gpiochip: int, reason: str) -> None:
        super().__init__(
            "connect",
            reason=f"could not open the GPIO engine on /dev/gpiochip{gpiochip}: {reason}",
            check=(
                f"(1) run on a Raspberry Pi with lgpio installed (`apt install python3-lgpio`); "
                f"(2) ensure /dev/gpiochip{gpiochip} exists and the user is in the 'gpio' group; "
                f"(3) on a non-Pi host, run with --simulate"
            ),
            inputs={"gpiochip": gpiochip},
        )


class BufferTooLargeError(StimpyError):
    """The uploaded pattern exceeds the engine's frame-buffer cap."""

    def __init__(self, *, frames: int, max_frames: int) -> None:
        super().__init__(
            "set_pattern",
            reason=f"buffer of {frames} frames exceeds the engine cap of {max_frames}",
            check=(
                f"reduce the buffer to <= {max_frames} frames, or split it across multiple "
                "set_pattern calls"
            ),
            inputs={"frames": frames, "max_frames": max_frames},
        )


class EngineStateError(StimpyError):
    """An operation was requested in a state that does not allow it."""

    def __init__(self, operation: str, *, reason: str, check: str) -> None:
        super().__init__(operation, reason=reason, check=check)
