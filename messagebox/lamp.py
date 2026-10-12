"""Optional lamp output.

The arcade lamp is 12 V. The Pi only switches a transistor or MOSFET on
BCM GPIO 26 (physical pin 37). With the lamp disconnected, toggling that
pin is harmless. If GPIO cannot be opened, the box logs the pattern and
keeps running.

Patterns:

* ``off`` — queue empty
* ``waiting`` — notes are waiting (steady on, or a slow blink)
* ``recording`` — fast blink while the mic is open
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("messagebox.lamp")


@dataclass(frozen=True)
class LampPatterns:
    waiting: str = "steady"
    waiting_on_seconds: float = 0.15
    waiting_off_seconds: float = 1.6
    recording_on_seconds: float = 0.1
    recording_off_seconds: float = 0.1


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
        if mode == "off":
            return
        if mode == "recording":
            self._led.blink(
                on_time=self._patterns.recording_on_seconds,
                off_time=self._patterns.recording_off_seconds,
                background=True,
            )
            return
        if mode == "waiting" and self._patterns.waiting == "blink":
            self._led.blink(
                on_time=self._patterns.waiting_on_seconds,
                off_time=self._patterns.waiting_off_seconds,
                background=True,
            )
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
    from gpiozero import Device
    from gpiozero.pins.lgpio import LGPIOFactory

    if not isinstance(Device.pin_factory, LGPIOFactory):
        Device.pin_factory = LGPIOFactory()
