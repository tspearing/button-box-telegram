"""Short UI tones, generated as 16-bit PCM WAV files.

The tones are original (a few sine waves with a short fade). There are no
third-party sound files to license.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path


Segment = tuple[float, float, float]  # frequency Hz, duration seconds, silence after


def synth_wav(
    path: Path,
    segments: list[Segment],
    *,
    rate: int = 48000,
    volume: float = 0.35,
) -> None:
    """Write a mono WAV. ``volume`` is 0..1."""
    samples: list[int] = []
    for frequency, duration, gap in segments:
        count = max(1, int(rate * duration))
        fade = min(int(rate * 0.008), count // 2)
        for index in range(count):
            envelope = 1.0
            if fade and index < fade:
                envelope = index / fade
            elif fade and index > count - fade:
                envelope = (count - index) / fade
            value = math.sin(2 * math.pi * frequency * index / rate) * volume * envelope
            value = max(-1.0, min(1.0, value))
            samples.append(int(value * 32767))
        samples.extend([0] * int(rate * gap))
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for sample in samples:
        frames += struct.pack("<h", sample)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)


class SoundBank:
    """Cues written under the data directory on startup."""

    NAMES = ("start", "sent", "discard", "empty", "error")

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def ensure(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._write("start", [(880, 0.09, 0.0)])
        self._write("sent", [(660, 0.08, 0.04), (990, 0.12, 0.0)])
        self._write("discard", [(196, 0.14, 0.0)])
        self._write("empty", [(523, 0.07, 0.0)], volume=0.22)
        self._write("error", [(165, 0.08, 0.03), (130, 0.14, 0.0)])

    def path(self, name: str) -> Path:
        if name not in self.NAMES:
            raise KeyError(name)
        return self.directory / f"{name}.wav"

    def _write(self, name: str, segments: list[Segment], *, volume: float = 0.35) -> None:
        synth_wav(self.path(name), segments, volume=volume)
