"""Spoken card names.

espeak-ng is used because it installs from apt on Raspberry Pi OS and
needs no voice download. A per-card wav file, when it exists, is played
instead. Synthesis and playback failures are reported to the caller.
They must not take the service down. The speaker may be unplugged.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("messagebox.announce")

MAX_ANNOUNCE_CHARS = 80


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


class Announcer:
    """Resolve the wav for a tap. ``synth(text, dest) -> bool`` is injectable."""

    def __init__(self, cache_dir: Path, synth) -> None:
        self.cache_dir = Path(cache_dir)
        self.synth = synth
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
        digest = hashlib.sha256(spoken.encode("utf-8")).hexdigest()[:16]
        dest = self.cache_dir / f"{digest}.wav"
        with self._lock:
            if dest.is_file() and dest.stat().st_size > 44:
                return dest
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            try:
                ok = bool(self.synth(spoken, dest))
            except Exception:
                log.exception("speech synthesis failed")
                dest.unlink(missing_ok=True)
                return None
            if not ok or not dest.is_file() or dest.stat().st_size <= 44:
                log.warning("speech synthesis produced no audio for %r", spoken)
                dest.unlink(missing_ok=True)
                return None
            return dest
