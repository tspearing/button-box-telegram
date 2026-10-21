"""Button and card inputs.

``ButtonSource`` is the arcade button (GPIO on the Pi, keyboard in dev mode).
``CardSource`` is the PN532. The driver is ``messagebox.inputs.pn532``.
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
    """PN532 over I2C. ``on_card(uid)`` runs when a tag is newly presented.

    The callback must only queue the UID. The runtime selects the chat.
    """

    def start(self, on_card: Callable[[str], None]) -> None: ...

    def stop(self) -> None: ...
