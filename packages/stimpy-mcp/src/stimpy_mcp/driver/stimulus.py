"""Semantic stimulus control over a :class:`StimulusEngine`.

Validates tool inputs (actionable :class:`ValidationError`s before anything
reaches the engine), builds :class:`Pattern`s, stages/swaps/stops, and reports
status. Engine-agnostic: works against the simulator or lgpio identically.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from stimpy_mcp.core.enums import RunMode
from stimpy_mcp.core.errors import EngineStateError, ValidationError
from stimpy_mcp.core.models import EngineStatus, Pattern, PinMap, StagedHandle
from stimpy_mcp.engine.base import StimulusEngine


class StimulusDriver:
    """Validate -> stage -> go-live/stop -> status, over a single engine."""

    def __init__(
        self,
        engine: StimulusEngine,
        pin_map: PinMap,
        *,
        default_clock_rate_hz: float,
    ) -> None:
        self._engine = engine
        self._pin_map = pin_map
        self._clock_rate_hz = default_clock_rate_hz
        self._word_max = (1 << pin_map.channel_count) - 1
        self._frames: tuple[int, ...] | None = None
        self._sync_frames: frozenset[int] = frozenset({0})
        self._staged: StagedHandle | None = None

    @property
    def pin_map(self) -> PinMap:
        return self._pin_map

    # -- operations -------------------------------------------------------
    def set_pattern(
        self,
        frames: Sequence[int],
        *,
        sync_frames: Iterable[int] | None = None,
        clock_rate_hz: float | None = None,
        go_live: bool = True,
    ) -> EngineStatus:
        op = "set_pattern"
        words = self._validate_frames(op, frames)
        syncs = self._validate_sync_frames(op, sync_frames, len(words))
        if clock_rate_hz is not None:
            rate = self._validate_clock(op, clock_rate_hz)
        else:
            rate = self._clock_rate_hz

        pattern = Pattern(frames=words, sync_frames=syncs, clock_rate_hz=rate)
        handle = self._engine.stage_pattern(pattern)  # may raise BufferTooLargeError

        self._frames = words
        self._sync_frames = syncs
        self._clock_rate_hz = rate
        self._staged = handle
        if go_live:
            self._engine.go_live(handle, mode=RunMode.REPEAT)
        return self._engine.status()

    def set_clock_rate(self, clock_rate_hz: float) -> EngineStatus:
        op = "set_clock_rate"
        rate = self._validate_clock(op, clock_rate_hz)
        self._clock_rate_hz = rate
        running = self._engine.status().running
        if self._frames is not None:
            # Restage at the new rate; swap in if currently running.
            pattern = Pattern(
                frames=self._frames, sync_frames=self._sync_frames, clock_rate_hz=rate
            )
            self._staged = self._engine.stage_pattern(pattern)
            if running:
                self._engine.go_live(self._staged, mode=RunMode.REPEAT)
        return self._engine.status()

    def start(self, mode: RunMode = RunMode.REPEAT) -> EngineStatus:
        if self._staged is None:
            raise EngineStateError(
                "start",
                reason="no pattern has been staged",
                check="call set_pattern first (or load_counter if enabled)",
            )
        self._engine.go_live(self._staged, mode=mode)
        return self._engine.status()

    def stop(self) -> EngineStatus:
        self._engine.stop()
        return self._engine.status()

    def status(self) -> EngineStatus:
        return self._engine.status()

    def close(self) -> None:
        self._engine.close()

    # -- validation -------------------------------------------------------
    def _validate_frames(self, op: str, frames: Sequence[int]) -> tuple[int, ...]:
        if not frames:
            raise ValidationError(
                op, parameter="frames", value=frames, requirement="at least 1 frame"
            )
        max_frames = self._engine.limits().max_frames_per_wave
        if len(frames) > max_frames:
            # Surfaced here as validation; stage_pattern also enforces it authoritatively.
            raise ValidationError(
                op, parameter="frames", value=len(frames),
                requirement=f"<= {max_frames} frames (engine buffer cap)",
            )
        out: list[int] = []
        for i, w in enumerate(frames):
            if not isinstance(w, int) or isinstance(w, bool):
                raise ValidationError(
                    op, parameter=f"frames[{i}]", value=w, requirement="an integer"
                )
            if not 0 <= w <= self._word_max:
                raise ValidationError(
                    op, parameter=f"frames[{i}]", value=w,
                    requirement=f"0 <= word <= {self._word_max} (0x{self._word_max:X})",
                )
            out.append(w)
        return tuple(out)

    def _validate_sync_frames(
        self, op: str, sync_frames: Iterable[int] | None, n: int
    ) -> frozenset[int]:
        if sync_frames is None:
            return frozenset({0})
        out: set[int] = set()
        for s in sync_frames:
            if not isinstance(s, int) or isinstance(s, bool) or not 0 <= s < n:
                raise ValidationError(
                    op, parameter="sync_frames", value=s,
                    requirement=f"each index in 0..{n - 1}",
                )
            out.add(s)
        return frozenset(out)

    def _validate_clock(self, op: str, clock_rate_hz: float) -> float:
        if not isinstance(clock_rate_hz, (int, float)) or isinstance(clock_rate_hz, bool):
            raise ValidationError(
                op, parameter="clock_rate_hz", value=clock_rate_hz, requirement="a number"
            )
        if clock_rate_hz <= 0:
            raise ValidationError(
                op, parameter="clock_rate_hz", value=clock_rate_hz, requirement="> 0 Hz"
            )
        return float(clock_rate_hz)
