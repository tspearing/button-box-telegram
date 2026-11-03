"""Optional lamp output.

The arcade lamp is 12 V. The Pi only switches a transistor or MOSFET on
BCM GPIO 26 (physical pin 37). With the lamp disconnected, toggling that
pin is harmless. If GPIO cannot be opened, the box logs the pattern and
keeps running.

Patterns:

* ``off`` — nothing unheard in any chat
* ``waiting`` — the focused chat has unheard notes (a short flash every 3 seconds)
* ``elsewhere`` — only some other chat has unheard notes (1 second on, 1 second off)
* ``recording`` — steady on, and only while the microphone is open
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from messagebox.workdir import prepare_lgpio_directory

log = logging.getLogger("messagebox.lamp")


@dataclass(frozen=True)
class LampPatterns:
    unheard_on_seconds: float = 0.5
    unheard_off_seconds: float = 2.5
    elsewhere_on_seconds: float = 1.0
    elsewhere_off_seconds: float = 1.0


def lamp_output(mode: str, patterns: LampPatterns) -> tuple[str, float, float]:
    """How a mode drives the pin: ``off``, ``on``, or ``blink`` with timings.

    Recording is a steady light. This chat's notes are a short flash.
    Another chat's notes are an even blink.
    """
    if mode == "off":
        return ("off", 0.0, 0.0)
    if mode == "recording":
        return ("on", 0.0, 0.0)
    if mode == "waiting":
        return ("blink", patterns.unheard_on_seconds, patterns.unheard_off_seconds)
    if mode == "elsewhere":
        return ("blink", patterns.elsewhere_on_seconds, patterns.elsewhere_off_seconds)
    return ("on", 0.0, 0.0)


class NullLamp:
    """Lamp stand-in used in dev mode and when GPIO is unavailable."""

    def __init__(self) -> None:
        self.mode = "off"

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        log.info("lamp %s", mode)

    def close(self) -> None:
        self.mode = "off"


class GpioLamp:
    """Drive a logic-level transistor from a gpiozero LED."""

    def __init__(self, pin: int, *, active_high: bool, patterns: LampPatterns) -> None:
        prepare_lgpio_directory(Path(os.environ.get("LG_WD", "/var/lib/message-box")))
        from gpiozero import LED

        self._led = LED(pin, active_high=active_high)
        self._patterns = patterns
        self.mode: str | None = None
        self._led.off()

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        self._led.off()
        kind, on_time, off_time = lamp_output(mode, self._patterns)
        if kind == "off":
            return
        if kind == "blink":
            self._led.blink(on_time=on_time, off_time=off_time, background=True)
            return
        self._led.on()

    def close(self) -> None:
        self._led.close()
        self.mode = "off"


def open_lamp(*, enabled: bool, pin: int, active_high: bool, patterns: LampPatterns, dev: bool):
    """Open the lamp, or a no-op lamp if it is disabled or GPIO is missing."""
    if dev or not enabled:
        lamp = NullLamp()
        if not enabled:
            log.info("lamp output disabled in config")
        return lamp
    try:
        _use_lgpio_factory()
        return GpioLamp(pin, active_high=active_high, patterns=patterns)
    except Exception as exc:  # gpiozero raises a wide family of errors on a laptop
        log.warning("lamp disabled, GPIO is not available (%s). The box will keep running.", exc)
        return NullLamp()


def _use_lgpio_factory() -> None:
    """Pin factory for the Pi 5. gpiozero then talks to lgpio, not RPi.GPIO."""
    prepare_lgpio_directory(Path(os.environ.get("LG_WD", "/var/lib/message-box")))
    from gpiozero import Device
    from gpiozero.pins.lgpio import LGPIOFactory

    if not isinstance(Device.pin_factory, LGPIOFactory):
        Device.pin_factory = LGPIOFactory()
