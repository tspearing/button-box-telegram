"""Press-vs-hold state machine for the arcade button.

The machine is pure logic. GPIO, the keyboard simulator, and the clock all
live outside it, which is what makes hold versus short-press testable.

Timeline for a recording (defaults):

    press -------- 0.6s -------- beep / record -------- release
                  hold threshold                         stop

Audio captured before the hold threshold does not exist; the child waits
for the beep. Audio shorter than ``min_record_seconds`` is discarded.
Audio is cut off at ``max_record_seconds`` even if the button is still down.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Phase(Enum):
    IDLE = "idle"
    DOWN = "down"
    RECORDING = "recording"
    HELD_AFTER_MAX = "held_after_max"


class ActionKind(Enum):
    SHORT_PRESS = "short_press"
    RECORD_START = "record_start"
    RECORD_STOP = "record_stop"
    RECORD_DISCARD = "record_discard"
    RECORD_MAX = "record_max"
    HOLD_CLEARED = "hold_cleared"


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    held_for: float = 0.0
    recorded_for: float = 0.0


class ButtonMachine:
    """Turn press, release, and time ticks into high-level actions."""

    def __init__(
        self,
        *,
        hold_seconds: float = 0.6,
        min_record_seconds: float = 1.0,
        max_record_seconds: float = 120.0,
        debounce_seconds: float = 0.03,
    ) -> None:
        if hold_seconds <= 0:
            raise ValueError("hold_seconds must be positive")
        if min_record_seconds <= 0:
            raise ValueError("min_record_seconds must be positive")
        if max_record_seconds <= min_record_seconds:
            raise ValueError("max_record_seconds must be greater than min_record_seconds")
        if debounce_seconds < 0:
            raise ValueError("debounce_seconds cannot be negative")
        if debounce_seconds >= hold_seconds:
            raise ValueError("debounce_seconds must be shorter than hold_seconds")
        self.hold_seconds = hold_seconds
        self.min_record_seconds = min_record_seconds
        self.max_record_seconds = max_record_seconds
        self.debounce_seconds = debounce_seconds
        self.phase = Phase.IDLE
        self._down_at: float | None = None
        self._record_at: float | None = None

    def press(self, now: float) -> list[Action]:
        """Button went down. Ignored if it was already down."""
        if self.phase is not Phase.IDLE:
            return []
        self.phase = Phase.DOWN
        self._down_at = now
        return []

    def release(self, now: float) -> list[Action]:
        """Button came up. Decides short press, stop, or discard."""
        if self.phase is Phase.IDLE:
            return []
        if self.phase is Phase.HELD_AFTER_MAX:
            self._clear()
            return [Action(ActionKind.HOLD_CLEARED)]
        if self.phase is Phase.DOWN:
            return self._release_before_recording(now)
        return self._release_during_recording(now)

    def tick(self, now: float) -> list[Action]:
        """Time passed while the button may still be down."""
        if self.phase is Phase.DOWN and self._down_at is not None:
            held = now - self._down_at
            if held >= self.hold_seconds:
                self.phase = Phase.RECORDING
                self._record_at = self._down_at + self.hold_seconds
                return [Action(ActionKind.RECORD_START, held_for=held, recorded_for=0.0)]
        if self.phase is Phase.RECORDING and self._record_at is not None:
            recorded = now - self._record_at
            if recorded >= self.max_record_seconds:
                held = now - (self._down_at if self._down_at is not None else now)
                self.phase = Phase.HELD_AFTER_MAX
                self._record_at = None
                return [
                    Action(
                        ActionKind.RECORD_MAX,
                        held_for=held,
                        recorded_for=self.max_record_seconds,
                    )
                ]
        return []

    def _release_before_recording(self, now: float) -> list[Action]:
        down_at = self._down_at if self._down_at is not None else now
        held = max(0.0, now - down_at)
        # Released in the same moment the threshold was crossed, before tick
        # noticed. Treat it as a recording that barely started.
        if held >= self.hold_seconds:
            recorded = held - self.hold_seconds
            self._clear()
            start = Action(ActionKind.RECORD_START, held_for=held, recorded_for=0.0)
            if recorded < self.min_record_seconds:
                end = Action(ActionKind.RECORD_DISCARD, held_for=held, recorded_for=recorded)
            else:
                end = Action(ActionKind.RECORD_STOP, held_for=held, recorded_for=recorded)
            return [start, end]
        self._clear()
        if held < self.debounce_seconds:
            return []
        return [Action(ActionKind.SHORT_PRESS, held_for=held)]

    def _release_during_recording(self, now: float) -> list[Action]:
        record_at = self._record_at if self._record_at is not None else now
        down_at = self._down_at if self._down_at is not None else now
        recorded = max(0.0, now - record_at)
        held = max(0.0, now - down_at)
        self._clear()
        if recorded < self.min_record_seconds:
            return [Action(ActionKind.RECORD_DISCARD, held_for=held, recorded_for=recorded)]
        return [Action(ActionKind.RECORD_STOP, held_for=held, recorded_for=recorded)]

    def _clear(self) -> None:
        self.phase = Phase.IDLE
        self._down_at = None
        self._record_at = None
