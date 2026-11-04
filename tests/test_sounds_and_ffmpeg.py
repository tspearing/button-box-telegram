"""Generated cues, and ffmpeg actually finalizing an OGG when recording stops."""

import shutil
import signal
import struct
import subprocess
import time
import wave
from pathlib import Path

import pytest

from messagebox.audio import AudioEngine, playback_command, record_command
from messagebox.config import AudioConfig
from messagebox.sounds import SoundBank


def test_sound_bank_writes_wavs(tmp_path):
    bank = SoundBank(tmp_path / "sounds")
    bank.ensure()
    for name in SoundBank.NAMES:
        data = bank.path(name).read_bytes()
        assert data[:4] == b"RIFF"
        assert len(data) > 44


def test_record_command_is_ogg_opus_and_does_not_steal_stdin(tmp_path):
    output = tmp_path / "note.ogg"
    command = record_command(
        source_args=["-f", "lavfi", "-i", "anullsrc"],
        output=output,
        bitrate="32k",
        max_seconds=120,
    )
    assert command[0] == "ffmpeg"
    assert "-nostdin" in command
    assert "libopus" in command
    assert "ogg" in command
    assert command[-1] == str(output)
    play = playback_command(output, "plughw:2,0")
    assert "-nostdin" in play
    assert "alsa" in play
    assert play[-1] == "plughw:2,0"
    assert "anoisesrc=" in " ".join(play)
    assert play.count("ffmpeg") == 1
    quiet = playback_command(output, "plughw:2,0", lead_in_ms=0)
    assert "anoisesrc=" not in " ".join(quiet)
    assert "anullsrc=" not in " ".join(quiet)
    silence = playback_command(output, "plughw:2,0", lead_in_mode="silence")
    assert "anullsrc=" in " ".join(silence)
    longer = playback_command(output, "plughw:2,0", lead_in_ms=450)
    assert "-t" in longer
    assert "0.450" in longer
    engine = AudioEngine(
        AudioConfig(
            mic_device="plughw:1,0",
            speaker_device="plughw:2,0",
            lead_in_ms=450,
            lead_in_mode="noise",
        )
    )
    assert "anoisesrc=" in " ".join(engine.play_cmd(output))
    assert "0.450" in engine.play_cmd(output)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_noise_lead_in_is_nonzero_and_one_stream(tmp_path):
    source = tmp_path / "tone.wav"
    with wave.open(str(source), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(struct.pack("<h", 8000) * 4800)  # 100 ms
    dest = tmp_path / "with-lead.wav"
    command = playback_command(
        source,
        "plughw:2,0",
        lead_in_ms=300,
        lead_in_mode="noise",
        lead_in_level_db=-60,
        destination=dest,
    )
    assert command.count("ffmpeg") == 1
    assert "-f" not in command or command[command.index("-f") + 1] == "lavfi"
    completed = subprocess.run(command, check=False, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    with wave.open(str(dest), "rb") as handle:
        seconds = handle.getnframes() / handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    assert 0.35 < seconds < 0.5
    samples = struct.unpack("<" + "h" * (len(raw) // 2), raw)
    left = samples[0::2]
    head = left[: 48000 // 10]
    body = left[int(48000 * 0.32) : int(48000 * 0.36)]
    assert max(abs(sample) for sample in head) > 0
    assert max(abs(sample) for sample in head) < 200
    assert sum(abs(sample) for sample in body) / len(body) > 1000


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_sigint_leaves_a_readable_ogg(tmp_path):
    output = tmp_path / "note.ogg"
    command = record_command(
        source_args=["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"],
        output=output,
        bitrate="32k",
        max_seconds=30,
    )
    proc = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(0.8)
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            pytest.fail("ffmpeg did not exit after SIGINT")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    assert output.is_file()
    assert output.stat().st_size > 200
    # Confirm the container is actually readable.
    probe = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )
    assert probe.returncode == 0, probe.stderr.decode()[-400:]
    assert isinstance(output, Path)
