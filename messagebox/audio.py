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


def lead_in_amplitude(level_db: float) -> float:
    """Linear amplitude for a dBFS level. -60 dB is 0.001 of full scale."""
    return 10 ** (float(level_db) / 20.0)


def rewind_offset(
    *,
    origin: float,
    elapsed: float,
    lead_in: float,
    rewind_seconds: float,
    jumps: int = 1,
) -> float:
    """Where to resume after jumping back, never before the start of the file.

    ``elapsed`` is wall time since this playback process started. The lead-in
    only wakes the speaker, so it does not count as time into the note.
    """
    played = max(0.0, float(elapsed) - max(0.0, float(lead_in)))
    position = max(0.0, float(origin)) + played
    back = max(0.0, float(rewind_seconds)) * max(1, int(jumps))
    return max(0.0, position - back)


def playback_command(
    path: Path,
    device: str,
    *,
    lead_in_ms: int = 300,
    lead_in_mode: str = "noise",
    lead_in_level_db: float = -60.0,
    start_seconds: float = 0.0,
    destination: Path | None = None,
) -> list[str]:
    """Play ``path`` in one ffmpeg process.

    The lead-in and the clip are one filter graph and one output. Digital
    silence does not wake a speaker that sleeps until it sees a signal, so
    the default lead-in is quiet noise. ``destination`` writes a wav instead
    of opening ALSA, for the diagnostic command.
    """
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
    ]
    if lead_in_ms > 0:
        seconds = f"{int(lead_in_ms) / 1000:.3f}"
        if lead_in_mode == "silence":
            source = "anullsrc=r=48000:cl=mono"
        else:
            amplitude = lead_in_amplitude(lead_in_level_db)
            source = (
                "anoisesrc=color=white:"
                f"amplitude={amplitude:.8f}:sample_rate=48000"
            )
        command.extend(["-f", "lavfi", "-t", seconds, "-i", source])
        # Seek the note only. The lead-in stays input 0 so the speaker still
        # wakes, and -ss is not applied to that noise.
        if start_seconds > 0:
            command.extend(["-ss", f"{float(start_seconds):.3f}"])
        command.extend(["-i", str(path)])
        # The clip is resampled without dropping its first samples. The
        # lead-in is input 0, so those samples reach the speaker first.
        command.extend(
            [
                "-filter_complex",
                "[0:a]aformat=sample_fmts=s16:channel_layouts=stereo:sample_rates=48000[lead];"
                "[1:a]aresample=async=0:first_pts=0,"
                "aformat=sample_fmts=s16:channel_layouts=stereo:sample_rates=48000[clip];"
                "[lead][clip]concat=n=2:v=0:a=1[out]",
                "-map",
                "[out]",
            ]
        )
    else:
        if start_seconds > 0:
            command.extend(["-ss", f"{float(start_seconds):.3f}"])
        command.extend(["-i", str(path), "-ac", "2", "-ar", "48000"])
    if destination is None:
        command.extend(["-f", "alsa", device])
    else:
        command.append(str(destination))
    return command


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

    def fallback_notes(self) -> list[str]:
        """Warnings when auto-detect did not find a USB mic or speaker.

        An explicit ``mic_device`` or ``speaker_device`` is never reported
        here. Playback through ALSA's ``default`` device can exit 0 and
        still be silent, so the check command has to say so itself.
        """
        mic = self.resolve_mic()
        speaker = self.resolve_speaker(mic_device=mic)
        notes: list[str] = []
        if self.config.mic_device in ("", "auto") and mic == "default":
            notes.append(
                "no USB microphone detected, recording used ALSA default and may fail or be silent"
            )
        if self.config.speaker_device in ("", "auto") and speaker == "default":
            notes.append(
                "no USB speaker detected, playback went to ALSA default and may be silent"
            )
        return notes

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

    def play_cmd(self, path: Path, *, start_seconds: float = 0.0) -> list[str]:
        return playback_command(
            path,
            self.resolve_speaker(),
            lead_in_ms=self.config.lead_in_ms,
            lead_in_mode=self.config.lead_in_mode,
            lead_in_level_db=self.config.lead_in_level_db,
            start_seconds=start_seconds,
        )

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

    async def play(self, path: Path, *, start_seconds: float = 0.0) -> asyncio.subprocess.Process:
        command = self.play_cmd(path, start_seconds=start_seconds)
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
