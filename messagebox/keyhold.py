"""Classify terminal key-repeat into button press and release edges.

A terminal does not report key-up. Holding a key produces one character,
a pause (the repeat delay), then a stream of repeats. This helper waits
long enough to tell a tap from a hold, then emits timestamps the button
state machine can consume.

* A tap (no repeat before ``window`` seconds) becomes a short press.
* Repeats mean the key is held. When the repeats stop for ``gap`` seconds,
  the button is released.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeyEdge:
    kind: str  # "press" or "release"
    at: float


class KeyHoldClassifier:
    def __init__(
        self,
        *,
        window: float = 0.7,
        gap: float = 0.22,
        tap_seconds: float = 0.12,
    ) -> None:
        if window <= 0 or gap <= 0 or tap_seconds <= 0:
            raise ValueError("window, gap, and tap_seconds must be positive")
        if tap_seconds >= window:
            raise ValueError("tap_seconds must be shorter than window")
        self.window = window
        self.gap = gap
        self.tap_seconds = tap_seconds
        self._t0: float | None = None
        self._last: float | None = None
        self._holding = False

    @property
    def pending(self) -> bool:
        return self._t0 is not None

    def key_down(self, now: float) -> list[KeyEdge]:
        if self._t0 is None:
            self._t0 = now
            self._last = now
            self._holding = False
            return []
        self._last = now
        if self._holding:
            return []
        self._holding = True
        return [KeyEdge("press", self._t0)]

    def poll(self, now: float) -> list[KeyEdge]:
        if self._t0 is None or self._last is None:
            return []
        if not self._holding:
            if now - self._t0 >= self.window:
                started = self._t0
                self._reset()
                return [
                    KeyEdge("press", started),
                    KeyEdge("release", started + self.tap_seconds),
                ]
            return []
        if now - self._last >= self.gap:
            self._reset()
            return [KeyEdge("release", now)]
        return []

    def _reset(self) -> None:
        self._t0 = None
        self._last = None
        self._holding = False
