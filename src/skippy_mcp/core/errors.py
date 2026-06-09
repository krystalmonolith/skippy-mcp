"""Error model.

Every user-facing failure is a :class:`SkippyError`. Its string form follows the
actionable-message contract: it names the **operation**, the **input** that
provoked it, the **reason** it failed, and what to **check** or do next. No bare
stack traces and no "X failed" reach the user.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skippy_mcp.core.models import IdnInfo


class SkippyError(Exception):
    """Base for all SkippyMCP errors; ``str(self)`` is an actionable message."""

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


class ConfigError(SkippyError):
    """Server configuration is invalid (e.g. no target address supplied)."""


class ConnectionFailedError(SkippyError):
    """Could not open the VISA resource."""

    def __init__(self, resource: str, *, reason: str) -> None:
        super().__init__(
            "connect",
            reason=f"could not open {resource!r}: {reason}",
            check=(
                "(1) the scope is powered and on the LAN; (2) the address is "
                "correct (Utility -> IO on the front panel); (3) no firewall "
                "blocks the connection"
            ),
            inputs={"resource": resource},
        )


class InstrumentTimeoutError(SkippyError):
    """A write/query exceeded the VISA timeout."""

    def __init__(self, operation: str, *, command: str, timeout_ms: int) -> None:
        super().__init__(
            operation,
            reason=(
                f"command {command!r} timed out after {timeout_ms} ms; "
                "the scope did not respond"
            ),
            check=(
                "(1) the scope is powered and on the LAN; (2) the configured "
                "address matches Utility -> IO; (3) no modal dialog on the front "
                "panel is blocking remote control"
            ),
            inputs={"command": command},
        )


class UnsupportedInstrumentError(SkippyError):
    """No registered dialect matched the identified instrument."""

    def __init__(self, idn: IdnInfo, supported: Iterable[str]) -> None:
        series = ", ".join(supported)
        super().__init__(
            "connect",
            reason=f"no dialect supports model {idn.model!r}",
            check=f"connect a supported instrument; known series: {series}",
            inputs={"manufacturer": idn.manufacturer, "model": idn.model},
        )


class NotSupportedError(SkippyError):
    """The requested operation is not valid for the connected instrument."""

    def __init__(self, operation: str, *, series: str, reason: str) -> None:
        super().__init__(
            operation,
            reason=f"not supported on the {series} series: {reason}",
            check="use an instrument/option that provides this capability",
        )


class ValidationError(SkippyError):
    """A tool input failed pre-validation before any SCPI was sent."""

    def __init__(self, operation: str, *, parameter: str, value: object, requirement: str) -> None:
        super().__init__(
            operation,
            reason=f"parameter {parameter!r} is out of range or invalid",
            check=f"{parameter} must satisfy: {requirement}",
            inputs={parameter: value},
        )


class ScpiError(SkippyError):
    """The instrument reported an error via ``:SYSTem:ERRor?``."""

    def __init__(
        self,
        operation: str,
        *,
        code: int,
        message: str,
        command: str | None = None,
    ) -> None:
        self.code = code
        inputs: dict[str, object] = {"command": command} if command is not None else {}
        super().__init__(
            operation,
            reason=f"the scope reported SCPI error {code}: {message}",
            check="verify the command is valid for this model and parameters are in range",
            inputs=inputs,
        )
