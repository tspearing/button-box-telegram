"""Dev-mode button: the keyboard stands in for the arcade button.

A terminal does not report key-up, so SPACE is interpreted with
:class:`messagebox.keyhold.KeyHoldClassifier` (a tap versus key repeat).
Two extra keys do not depend on repeat:

* ``s`` — short press (play the next note)
* ``r`` — press once to hold, press again to release
* ``q`` — quit
"""

from __future__ import annotations

import logging
import os
import select
import sys
import termios
import threading
import time
import tty

from messagebox.inputs.base import ButtonEdge
from messagebox.keyhold import KeyHoldClassifier

log = logging.getLogger("messagebox.keyboard")

BANNER = """
Message Box dev mode. This terminal is the arcade button.

  Hold SPACE     record (let go to stop). A quick tap plays the next note.
                 The keyboard must repeat keys. If a tap starts a recording,
                 raise dev.repeat_window_seconds above the key-repeat delay.
  s              short press, if SPACE is awkward
  r              start a hold, press r again to release
  q              quit
""".strip()


class KeyboardButtonSource:
    def __init__(self, *, window: float, gap: float) -> None:
        self._classifier = KeyHoldClassifier(window=window, gap=gap)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._r_held = False

    @property
    def quit_requested(self) -> bool:
        return self._stop.is_set()

    def start(self, on_edge) -> None:
        if not sys.stdin.isatty():
            raise SystemExit(
                "Dev mode needs a terminal. Run it in the foreground, without a pipe."
            )
        self._stop.clear()
        self._r_held = False
        self._thread = threading.Thread(
            target=self._loop,
            args=(on_edge,),
            name="messagebox-keyboard",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _loop(self, on_edge) -> None:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        print(BANNER, flush=True)
        try:
            tty.setcbreak(fd)
            while not self._stop.is_set():
                ready, _, _ = select.select([fd], [], [], 0.05)
                now = time.monotonic()
                if ready:
                    self._handle_bytes(os.read(fd, 32), now, on_edge)
                if self._stop.is_set():
                    break
                for edge in self._classifier.poll(time.monotonic()):
                    _emit(on_edge, edge.kind, edge.at)
        except Exception:
            log.exception("keyboard input failed")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def _handle_bytes(self, chunk: bytes, now: float, on_edge) -> None:
        for char in chunk.decode("utf-8", errors="ignore"):
            if char in {"q", "Q"}:
                self._stop.set()
                return
            if char in {"s", "S"}:
                on_edge(ButtonEdge.PRESS, now)
                on_edge(ButtonEdge.RELEASE, now + 0.12)
                continue
            if char in {"r", "R"}:
                if self._r_held:
                    on_edge(ButtonEdge.RELEASE, now)
                    self._r_held = False
                else:
                    on_edge(ButtonEdge.PRESS, now)
                    self._r_held = True
                continue
            if char == " ":
                for edge in self._classifier.key_down(now):
                    _emit(on_edge, edge.kind, edge.at)


def _emit(on_edge, kind: str, at: float) -> None:
    edge = ButtonEdge.PRESS if kind == "press" else ButtonEdge.RELEASE
    on_edge(edge, at)
