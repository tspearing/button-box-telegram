"""ALSA capture and playback device selection.

``arecord -l`` lists microphones. ``aplay -l`` lists speakers. The Pi's own
headphone and HDMI cards are ignored so a USB mic and USB speaker win.
Config values other than ``auto``, and the ``MESSAGEBOX_MIC`` /
``MESSAGEBOX_SPEAKER`` environment variables, bypass detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_CARD_RE = re.compile(r"^card (\d+): ([^ \[]+) \[(.*?)\], device (\d+):")
_BUILTIN_MARKERS = ("bcm2835", "hdmi", "headphone", "vc4")


@dataclass(frozen=True)
class AlsaDevice:
    card: int
    device: int
    card_id: str
    name: str

    @property
    def plughw(self) -> str:
        return f"plughw:{self.card},{self.device}"

    def matches(self, hint: str) -> bool:
        blob = f"{self.card_id} {self.name}".lower()
        return hint.lower() in blob


def parse_alsa_list(text: str) -> list[AlsaDevice]:
    """Parse the card/device lines from ``arecord -l`` or ``aplay -l``."""
    devices: list[AlsaDevice] = []
    for line in text.splitlines():
        match = _CARD_RE.match(line.strip())
        if not match:
            continue
        devices.append(
            AlsaDevice(
                card=int(match.group(1)),
                device=int(match.group(4)),
                card_id=match.group(2),
                name=match.group(3),
            )
        )
    return devices


def is_builtin(device: AlsaDevice) -> bool:
    blob = f"{device.card_id} {device.name}".lower()
    return any(marker in blob for marker in _BUILTIN_MARKERS)


def choose_device(
    devices: list[AlsaDevice],
    hints: list[str] | tuple[str, ...],
    *,
    exclude_cards: set[int] | None = None,
) -> AlsaDevice | None:
    """Pick the best USB device.

    Earlier hints score higher, so ``TONOR`` beats a generic ``USB`` match.
    Built-in Pi cards are skipped. Returns None when nothing suitable exists;
    the caller can fall back to the ALSA ``default`` device.
    """
    excluded = exclude_cards or set()
    candidates = [
        device
        for device in devices
        if device.card not in excluded and not is_builtin(device)
    ]
    if not candidates:
        return None

    def score(device: AlsaDevice) -> int:
        total = 0
        for index, hint in enumerate(hints):
            if hint and device.matches(hint):
                total += 100 - index
        return total

    ranked = sorted(candidates, key=lambda device: (score(device), -device.card), reverse=True)
    return ranked[0]


def card_from_alsa_device(device: str) -> int | None:
    """Extract the card number from ``plughw:N,M`` or ``hw:N,M``."""
    match = re.match(r"^(?:plughw|hw):(\d+)", device.strip())
    if not match:
        return None
    return int(match.group(1))
