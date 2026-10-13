"""Arcade button on BCM GPIO 17, internal pull-up, active low.

gpiozero's default on a Pi 5 is the lgpio pin factory. We select it
explicitly so the process never falls back to RPi.GPIO, which does not
support the Pi 5.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from messagebox.inputs.base import ButtonEdge
from messagebox.workdir import prepare_lgpio_directory

log = logging.getLogger("messagebox.gpio")


class GpioButtonSource:
    def __init__(self, pin: int, *, bounce_seconds: float) -> None:
        self.pin = pin
        self.bounce_seconds = bounce_seconds
        self._button = None

    def start(self, on_edge) -> None:
        prepare_lgpio_directory(Path(os.environ.get("LG_WD", "/var/lib/message-box")))
        from gpiozero import Button, Device
        from gpiozero.pins.lgpio import LGPIOFactory

        if not isinstance(getattr(Device, "pin_factory", None), LGPIOFactory):
            Device.pin_factory = LGPIOFactory()
        self._button = Button(
            self.pin,
            pull_up=True,
            bounce_time=self.bounce_seconds if self.bounce_seconds > 0 else None,
        )

        # gpiozero calls these from its own thread, sometimes with the
        # device as an argument. The runtime hops back onto asyncio.
        self._button.when_pressed = lambda *_args: on_edge(ButtonEdge.PRESS, time.monotonic())
        self._button.when_released = lambda *_args: on_edge(ButtonEdge.RELEASE, time.monotonic())
        log.info("button on BCM %s, internal pull-up", self.pin)

    def stop(self) -> None:
        if self._button is not None:
            self._button.close()
            self._button = None
