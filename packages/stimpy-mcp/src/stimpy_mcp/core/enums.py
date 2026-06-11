"""Domain enums (plain StrEnum — no SCPI dual-token trick needed here)."""

from __future__ import annotations

from enum import StrEnum


class RunMode(StrEnum):
    """How a staged pattern is played."""

    REPEAT = "repeat"  # loop forever (REPEAT_SYNC swap at loop boundary)
    ONCE = "once"      # emit one pass then stop


class EngineState(StrEnum):
    """Coarse lifecycle state of the stimulus engine."""

    IDLE = "idle"        # configured, nothing staged/running
    RUNNING = "running"  # a pattern is being clocked out
    STOPPED = "stopped"  # was running, now halted (lines low)
