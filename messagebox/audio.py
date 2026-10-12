"""Record and play audio through ffmpeg and ALSA.

ffmpeg is told ``-nostdin`` so it cannot steal the keyboard from dev mode.
Recording is stopped with SIGINT, which makes ffmpeg finish the OGG page
instead of leaving a truncated file. ``-t`` is a backstop at the max length.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import signal
from pathlib import Path

from messagebox.alsa import (
    AlsaDevice,
    card_from_alsa_device,
    choose_device,
    parse_alsa_list,
)
from messagebox.config import AudioConfig

log = logging.getLogger("messagebox.audio")


def record_command(
    *,
    source_args: list[str],
    output: Path,
    bitrate: str,
    max_seconds: float,
) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        *source_args,
        "-t",
        f"{max_seconds:.3f}",
        "-c:a",
        "libopus",
        "-b:a",
        bitrate,
        "-vbr",
        "on",
        "-application",
        "voip",
        "-f",
        "ogg",
        str(output),
    ]


def alsa_record_source(device: str, *, rate: int, channels: int) -> list[str]:
    return ["-f", "alsa", "-ac", str(channels), "-ar", str(rate), "-i", device]


def playback_command(path: Path, device: str) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(path),
        "-ac",
        "2",
        "-ar",
        "48000",
        "-f",
        "alsa",
        device,
    ]


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


class AudioEngine:
    """Resolve ALSA devices and start ffmpeg processes."""

    def __init__(
        self,
        config: AudioConfig,
        *,
        capture_lister=None,
        playback_lister=None,
    ) -> None:
        self.config = config
        self._capture_lister = capture_lister or _arecord_list
        self._playback_lister = playback_lister or _aplay_list

    def resolve_mic(self) -> str:
        if self.config.mic_device and self.config.mic_device != "auto":
            log.info("mic device override: %s", self.config.mic_device)
            return self.config.mic_device
        chosen = choose_device(
            parse_alsa_list(self._capture_lister()),
            self.config.mic_name_hints,
        )
        if chosen is None:
            log.warning("no USB microphone found; using ALSA default")
            return "default"
        log.info("microphone: %s (%s)", chosen.plughw, chosen.name)
        return chosen.plughw

    def resolve_speaker(self, *, mic_device: str | None = None) -> str:
        if self.config.speaker_device and self.config.speaker_device != "auto":
            log.info("speaker device override: %s", self.config.speaker_device)
            return self.config.speaker_device
        if mic_device is None:
            mic_device = (
                self.resolve_mic() if self.config.mic_device == "auto" else self.config.mic_device
            )
        exclude: set[int] = set()
        mic_card = card_from_alsa_device(mic_device)
        if mic_card is not None:
            exclude.add(mic_card)
        chosen = choose_device(
            parse_alsa_list(self._playback_lister()),
            self.config.speaker_name_hints,
            exclude_cards=exclude,
        )
        if chosen is None:
            log.warning("no USB speaker found; using ALSA default")
            return "default"
        log.info("speaker: %s (%s)", chosen.plughw, chosen.name)
        return chosen.plughw

    def describe(self) -> str:
        mic_devices = parse_alsa_list(self._capture_lister())
        speaker_devices = parse_alsa_list(self._playback_lister())
        lines = ["Capture devices:"]
        lines.extend(_format_devices(mic_devices) or ["  (none)"])
        lines.append("Playback devices:")
        lines.extend(_format_devices(speaker_devices) or ["  (none)"])
        mic = self.resolve_mic()
        lines.append(f"Using microphone: {mic}")
        lines.append(f"Using speaker: {self.resolve_speaker(mic_device=mic)}")
        return "\n".join(lines)

    def record_cmd(self, output: Path, *, max_seconds: float) -> list[str]:
        return record_command(
            source_args=alsa_record_source(
                self.resolve_mic(),
                rate=self.config.sample_rate,
                channels=self.config.channels,
            ),
            output=output,
            bitrate=self.config.opus_bitrate,
            max_seconds=max_seconds,
        )

    def play_cmd(self, path: Path) -> list[str]:
        return playback_command(path, self.resolve_speaker())

    async def start_recording(self, output: Path, *, max_seconds: float) -> asyncio.subprocess.Process:
        output.parent.mkdir(parents=True, exist_ok=True)
        command = self.record_cmd(output, max_seconds=max_seconds)
        log.info("recording to %s", output.name)
        # stderr is discarded on purpose. A full pipe would stall ffmpeg,
        # and -loglevel error is enough: the caller checks the file size.
        return await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def stop_recording(self, proc: asyncio.subprocess.Process, output: Path) -> bool:
        """Stop ffmpeg and report whether ``output`` looks like a real note."""
        if proc.returncode is None:
            proc.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        size = output.stat().st_size if output.is_file() else 0
        # SIGINT exits 255, or a negative signal number. A finished -t limit exits 0.
        if size < 500:
            log.error("recording produced %s bytes (ffmpeg %s)", size, proc.returncode)
            return False
        if proc.returncode not in (0, 255) and not (proc.returncode is not None and proc.returncode < 0):
            log.error("ffmpeg exited %s", proc.returncode)
            return False
        return True

    async def play(self, path: Path) -> asyncio.subprocess.Process:
        command = self.play_cmd(path)
        return await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def play_and_wait(self, path: Path) -> bool:
        if not path.is_file():
            log.error("missing sound file %s", path)
            return False
        proc = await self.play(path)
        code = await proc.wait()
        if code != 0:
            log.error("playback failed (ffmpeg %s) for %s", code, path.name)
            return False
        return True


def _format_devices(devices: list[AlsaDevice]) -> list[str]:
    return [f"  card {device.card}: {device.card_id} [{device.name}] -> {device.plughw}" for device in devices]


def _arecord_list() -> str:
    return _run_list("arecord")


def _aplay_list() -> str:
    return _run_list("aplay")


def _run_list(tool: str) -> str:
    import subprocess

    if shutil.which(tool) is None:
        log.error("%s is not installed (apt install alsa-utils)", tool)
        return ""
    try:
        completed = subprocess.run(
            [tool, "-l"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("%s -l failed: %s", tool, exc)
        return ""
    return completed.stdout + completed.stderr
