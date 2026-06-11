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
    """The GPIO engine (pigpio daemon) could not be reached."""

    def __init__(self, *, host: str, port: int, reason: str) -> None:
        super().__init__(
            "connect",
            reason=f"could not connect to pigpiod at {host}:{port}: {reason}",
            check=(
                "(1) start the daemon: `sudo pigpiod -g -s 1` (or `systemctl start pigpiod`); "
                "(2) verify pigpio_host/pigpio_port; (3) on a non-Pi host, run with --simulate"
            ),
            inputs={"pigpio_host": host, "pigpio_port": port},
        )


class BufferTooLargeError(StimpyError):
    """The uploaded pattern exceeds what a single pigpio wave can hold."""

    def __init__(self, *, frames: int, max_frames: int) -> None:
        super().__init__(
            "set_pattern",
            reason=(
                f"buffer of {frames} frames exceeds the single-wave limit of "
                f"{max_frames} at this clock rate"
            ),
            check=(
                f"reduce the buffer to <= {max_frames} frames, raise the pigpiod sample tick "
                "(coarser timing), or wait for v2 wave-chaining"
            ),
            inputs={"frames": frames, "max_frames": max_frames},
        )


class EngineStateError(StimpyError):
    """An operation was requested in a state that does not allow it."""

    def __init__(self, operation: str, *, reason: str, check: str) -> None:
        super().__init__(operation, reason=reason, check=check)
