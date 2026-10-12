"""Button inputs.

``ButtonSource`` is the arcade button (GPIO on the Pi, keyboard in dev mode).
``CardSource`` is the slot a future PN532 V3 driver will fill. v1 does not
ship a card reader; see ``docs/ROADMAP.md``.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Protocol


class ButtonEdge(Enum):
    PRESS = "press"
    RELEASE = "release"


class ButtonSource(Protocol):
    def start(self, on_edge: Callable[[ButtonEdge, float], None]) -> None: ...

    def stop(self) -> None: ...


class CardSource(Protocol):
    """Future PN532 over I2C, reading NTAG215 card and fob uids.

    Not implemented in v1. A driver should call ``on_card(uid)`` when a tag
    is presented. The runtime will pass that uid to
    ``RecipientDirectory.select_card``.
    """

    def start(self, on_card: Callable[[str], None]) -> None: ...

    def stop(self) -> None: ...
