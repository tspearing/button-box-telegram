"""Spoken card names.

Piper is the default voice: an offline neural model, installed into the
same virtualenv as the box. espeak-ng is the fallback when Piper or its
voice file is missing. A per-card wav file, when it exists, is played
instead of either. Synthesis failures are reported and must not take the
service down. The speaker may be unplugged.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import threading
import wave
from pathlib import Path
from typing import Callable

log = logging.getLogger("messagebox.announce")

MAX_ANNOUNCE_CHARS = 80

Synth = Callable[[str, Path], bool]


def clean_announce_text(text: str) -> str:
    """One line, no control characters. The caller enforces the length limit."""
    cleaned = "".join(ch if ch.isprintable() and ch not in "\r\n\t" else " " for ch in text)
    return " ".join(cleaned.split())


def espeak_ng_command(text: str, dest: Path) -> list[str]:
    """argv for espeak-ng. The phrase is after ``--`` so it cannot be a flag."""
    return ["espeak-ng", "-w", str(dest), "-s", "132", "-v", "en", "--", text]


def espeak_ng_synth(text: str, dest: Path) -> bool:
    """Write a wav. False when espeak-ng is missing or fails. Does not raise."""
    if shutil.which("espeak-ng") is None:
        log.warning("espeak-ng is not installed; card announcements will be skipped")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            espeak_ng_command(text, dest),
            check=False,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("espeak-ng failed: %s", exc)
        return False
    if completed.returncode != 0:
        log.warning("espeak-ng exited %s", completed.returncode)
        return False
    return dest.is_file()


def resample_command(source: Path, dest: Path, *, rate: int) -> list[str]:
    """Resample without trimming the start of the clip.

    ``async`` stays off, so ffmpeg does not drop or insert samples to chase
    a timestamp. ``first_pts=0`` keeps the first sample at the start.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-af",
        f"aresample=resampler=swr:osr={int(rate)}:async=0:first_pts=0",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]


def resample_wav(source: Path, dest: Path, *, rate: int) -> bool:
    """Rewrite ``source`` as 16-bit mono PCM at ``rate`` Hz. Does not raise."""
    if shutil.which("ffmpeg") is None:
        log.warning("ffmpeg is not available to resample the announcement")
        return False
    # The suffix has to stay .wav or ffmpeg will not know the format.
    partial = dest.with_name(dest.stem + ".partial.wav")
    try:
        completed = subprocess.run(
            resample_command(source, partial, rate=rate),
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("could not resample the announcement: %s", exc)
        partial.unlink(missing_ok=True)
        return False
    if completed.returncode != 0 or not partial.is_file() or partial.stat().st_size <= 44:
        log.warning("ffmpeg could not resample the announcement")
        partial.unlink(missing_ok=True)
        return False
    partial.replace(dest)
    return True


_PIPER_VOICES: dict[str, object] = {}


def piper_synthesize_wav(model: Path, text: str, dest: Path, length_scale: float) -> None:
    """Speak ``text`` with the Piper model. Raises when Piper cannot be used.

    The loaded model is kept so a later card name does not pay the load
    cost again. Callers already run this off the event loop.
    """
    from piper import PiperVoice, SynthesisConfig

    key = str(model)
    voice = _PIPER_VOICES.get(key)
    if voice is None:
        voice = PiperVoice.load(key)
        _PIPER_VOICES[key] = voice
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as handle:
        voice.synthesize_wav(
            text,
            handle,
            syn_config=SynthesisConfig(length_scale=float(length_scale)),
        )


class PiperSpeaker:
    """Speak with one Piper voice, then resample to the box sample rate."""

    def __init__(
        self,
        model: Path,
        length_scale: float,
        *,
        sample_rate: int = 48000,
        synthesize: Callable[[Path, str, Path, float], None] | None = None,
        resample: Callable[..., bool] | None = None,
    ) -> None:
        self.model = Path(model)
        self.length_scale = float(length_scale)
        self.sample_rate = sample_rate
        self._synthesize = synthesize or piper_synthesize_wav
        self._resample = resample or resample_wav

    def __call__(self, text: str, dest: Path) -> bool:
        config = Path(str(self.model) + ".json")
        if not self.model.is_file() or not config.is_file():
            log.warning(
                "Piper voice %s is missing (need the .onnx and the .onnx.json); using espeak-ng",
                self.model,
            )
            return False
        raw = dest.with_name(dest.name + ".piper.wav")
        try:
            self._synthesize(self.model, text, raw, self.length_scale)
        except ImportError:
            log.warning("piper-tts is not installed; using espeak-ng")
            raw.unlink(missing_ok=True)
            return False
        except Exception as exc:
            log.warning("Piper could not speak (%s); using espeak-ng", exc)
            raw.unlink(missing_ok=True)
            return False
        if not raw.is_file() or raw.stat().st_size <= 44:
            log.warning("Piper produced no audio; using espeak-ng")
            raw.unlink(missing_ok=True)
            return False
        if self._resample(raw, dest, rate=self.sample_rate):
            raw.unlink(missing_ok=True)
        else:
            raw.replace(dest)
        return dest.is_file() and dest.stat().st_size > 44


class SpeechAttempt:
    def __init__(self, cache_key: str, synth: Synth) -> None:
        self.cache_key = cache_key
        self.synth = synth


class Announcer:
    """Resolve the wav for a tap. Each attempt is ``(cache key, synth)``.

    The cache key includes the engine and voice, so a clip made by
    espeak-ng is not replayed after switching to Piper.
    """

    def __init__(
        self,
        cache_dir: Path,
        synth: Synth | None = None,
        *,
        attempts: list[SpeechAttempt] | None = None,
    ) -> None:
        if attempts is None:
            if synth is None:
                raise TypeError("announcer needs a synth")
            attempts = [SpeechAttempt("custom", synth)]
        self.cache_dir = Path(cache_dir)
        self.attempts = list(attempts)
        self._lock = threading.Lock()

    def clip_for(self, text: str, override: Path | None = None) -> Path | None:
        if override is not None:
            if override.is_file():
                return override
            log.warning("announce file %s is missing; falling back to speech", override)
        spoken = clean_announce_text(text)
        if not spoken:
            log.warning("no announcement text")
            return None
        if len(spoken) > MAX_ANNOUNCE_CHARS:
            spoken = spoken[:MAX_ANNOUNCE_CHARS]
        for attempt in self.attempts:
            clip = self._cached(spoken, attempt)
            if clip is not None:
                return clip
        return None

    def warm(self, items: list[tuple[str, Path | None]]) -> None:
        """Prepare configured names so the first tap is a cache hit."""
        for text, override in items:
            if not text and override is None:
                continue
            try:
                self.clip_for(text or "", override)
            except Exception:
                log.exception("could not prepare an announcement")

    def _cached(self, spoken: str, attempt: SpeechAttempt) -> Path | None:
        digest = hashlib.sha256(f"{attempt.cache_key}\n{spoken}".encode("utf-8")).hexdigest()[:16]
        dest = self.cache_dir / f"{digest}.wav"
        with self._lock:
            if dest.is_file() and dest.stat().st_size > 44:
                return dest
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            try:
                ok = bool(attempt.synth(spoken, dest))
            except Exception:
                log.exception("speech synthesis failed")
                dest.unlink(missing_ok=True)
                return None
            if not ok or not dest.is_file() or dest.stat().st_size <= 44:
                dest.unlink(missing_ok=True)
                return None
            return dest


def build_announcer(
    cache_dir: Path,
    *,
    engine: str,
    piper_voice: Path,
    piper_length_scale: float,
    sample_rate: int,
) -> Announcer:
    """Piper first, unless the config asks for espeak-ng only."""
    espeak = SpeechAttempt("espeak-ng", espeak_ng_synth)
    if engine == "espeak":
        return Announcer(cache_dir, attempts=[espeak])
    size = piper_voice.stat().st_size if piper_voice.is_file() else 0
    token = f"piper|{piper_voice}|{size}|{float(piper_length_scale):.3f}"
    piper = SpeechAttempt(
        token,
        PiperSpeaker(piper_voice, piper_length_scale, sample_rate=sample_rate),
    )
    return Announcer(cache_dir, attempts=[piper, espeak])
