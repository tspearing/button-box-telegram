"""Double-tap rewind while a voice note is playing.

Placeholder ids only. The chat -1000000000001 is a stand-in group.
"""

import asyncio
import shutil
import struct
import subprocess
import time
import wave
from pathlib import Path

import pytest

from messagebox.announce import Announcer
from messagebox.audio import AudioConfig, AudioEngine, playback_command, rewind_offset
from messagebox.button_machine import ButtonMachine
from messagebox.cards import build_directory
from messagebox.config import AppConfig, PathsConfig, TelegramConfig
from messagebox.coordinator import Coordinator, EffectKind
from messagebox.queue_store import Inbox, is_heard
from messagebox.runtime import Runtime
from messagebox.sounds import SoundBank

CHAT = -1000000000001
MOM = 123456789


def test_rewind_offset_clamps_to_the_start_and_ignores_the_lead_in():
    assert rewind_offset(origin=0, elapsed=15, lead_in=0.3, rewind_seconds=10) == pytest.approx(4.7)
    assert rewind_offset(origin=0, elapsed=3.3, lead_in=0.3, rewind_seconds=10) == 0
    # Still inside the wake-up lead-in: no progress through the note.
    assert rewind_offset(origin=5, elapsed=0.2, lead_in=0.3, rewind_seconds=10) == 0
    assert rewind_offset(origin=20, elapsed=0.2, lead_in=0.3, rewind_seconds=10) == 10
    assert rewind_offset(origin=20, elapsed=0, lead_in=0, rewind_seconds=10, jumps=2) == 0


def test_a_seek_keeps_one_wake_lead_in_and_does_not_seek_the_noise(tmp_path):
    path = tmp_path / "note.ogg"
    command = playback_command(path, "plughw:2,0", start_seconds=12.5)
    text = " ".join(command)
    assert text.count("anoisesrc=") == 1
    assert command.count("ffmpeg") == 1
    noise_at = command.index("anoisesrc=color=white:amplitude=0.00100000:sample_rate=48000")
    seek_at = command.index("-ss")
    file_at = command.index(str(path))
    assert noise_at < seek_at < file_at
    assert command[seek_at + 1] == "12.500"
    # The filter still plays the lead-in (input 0) before the note (input 1).
    graph = command[command.index("-filter_complex") + 1]
    assert "[lead][clip]concat=n=2" in graph
    untouched = playback_command(path, "plughw:2,0")
    assert "-ss" not in untouched


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_seek_starts_later_in_the_file(tmp_path):
    source = tmp_path / "tone.wav"
    with wave.open(str(source), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(struct.pack("<h", 8000) * 48000)  # 1 second
    dest = tmp_path / "from-half.wav"
    command = playback_command(
        source,
        "plughw:2,0",
        lead_in_ms=0,
        start_seconds=0.5,
        destination=dest,
    )
    completed = subprocess.run(command, check=False, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    with wave.open(str(dest), "rb") as handle:
        seconds = handle.getnframes() / handle.getframerate()
    assert 0.45 < seconds < 0.6


class _Proc:
    def __init__(self) -> None:
        self.returncode = None
        self._done = asyncio.Event()

    def finish(self, code: int = 0) -> None:
        if self.returncode is None:
            self.returncode = code
            self._done.set()

    def terminate(self) -> None:
        self.finish(-15)

    def kill(self) -> None:
        self.finish(-9)

    async def wait(self) -> int:
        await self._done.wait()
        return self.returncode or 0


class _Audio(AudioEngine):
    def __init__(self) -> None:
        super().__init__(
            AudioConfig(
                speaker_device="plughw:2,0",
                lead_in_mode="noise",
                lead_in_ms=300,
                lead_in_level_db=-60,
            )
        )
        self.starts: list[tuple[str, float]] = []
        self.commands: list[list[str]] = []
        self.procs: list[_Proc] = []

    async def play(self, path, start_seconds: float = 0.0):
        self.starts.append((Path(path).name, start_seconds))
        self.commands.append(self.play_cmd(path, start_seconds=start_seconds))
        proc = _Proc()
        self.procs.append(proc)
        return proc


def _speech(cache: Path, calls: list[str]) -> Announcer:
    def synth(text: str, dest: Path) -> bool:
        calls.append(text)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"RIFF" + b"\x00" * 80)
        return True

    return Announcer(cache, synth=synth)


def _runtime(tmp_path: Path, calls: list[str]) -> Runtime:
    runtime = Runtime(
        AppConfig(
            paths=PathsConfig(data_dir=tmp_path),
            telegram=TelegramConfig(allowed_chat_ids=(CHAT,), family_chat_id=CHAT),
        )
    )
    runtime.audio = _Audio()
    runtime._sounds = SoundBank(tmp_path / "sounds")
    runtime._sounds.ensure()
    runtime._inbox = Inbox(tmp_path / "inbox")
    runtime._directory = build_directory(CHAT, (), 120)
    runtime._coord = Coordinator(
        ButtonMachine(),
        runtime._inbox,
        focused_chat=lambda: CHAT,
        rewind_enabled=True,
        double_tap_window=0.375,
    )
    runtime._announcer = _speech(tmp_path / "cache", calls)
    return runtime


def _note(runtime: Runtime):
    assert runtime._inbox is not None
    return runtime._inbox.enqueue_bytes(
        b"OggS-note",
        meta={"source_chat_id": CHAT, "sender_id": MOM, "sender_name": "Mom", "sender_label": "Mom"},
    )


async def _until(pred) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


def _double_tap(runtime: Runtime) -> None:
    assert runtime._coord is not None
    runtime._coord.press(1.0)
    runtime._coord.release(1.1)
    runtime._coord.press(1.2)
    effects = runtime._coord.release(1.35)
    assert EffectKind.REWIND in [effect.kind for effect in effects]
    runtime._dispatch(effects)


def test_rewind_does_not_repeat_the_sender_and_seeks_the_note(tmp_path):
    calls: list[str] = []
    runtime = _runtime(tmp_path, calls)
    item = _note(runtime)
    audio: _Audio = runtime.audio

    async def scenario():
        runtime._coord.playing_id = item.id
        task = asyncio.create_task(runtime.begin_playback(item.id))
        await _until(lambda: len(audio.starts) == 1)
        audio.procs[0].finish(0)
        await _until(lambda: len(audio.starts) >= 2)
        assert calls == ["Message from Mom"]
        intro, note = audio.starts
        assert note[1] == 0
        runtime._segment_started = time.monotonic() - 12.3
        runtime._segment_origin = 0.0
        _double_tap(runtime)
        await _until(lambda: len(audio.starts) >= 3)
        assert calls == ["Message from Mom"]
        assert audio.starts[2][0] == item.path.name
        assert audio.starts[2][1] == pytest.approx(2.0, abs=0.05)
        seek = " ".join(audio.commands[2])
        assert seek.count("anoisesrc=") == 1
        assert "-ss" in audio.commands[2]
        assert intro[0] != item.path.name
        audio.procs[-1].finish(0)
        await task
        restored = runtime._inbox.get(item.id)
        assert restored is not None
        assert is_heard(restored.meta)

    asyncio.run(scenario())


def test_rewind_past_the_start_restarts_at_zero_without_the_introduction(tmp_path):
    calls: list[str] = []
    runtime = _runtime(tmp_path, calls)
    item = _note(runtime)
    audio: _Audio = runtime.audio

    async def scenario():
        runtime._coord.playing_id = item.id
        task = asyncio.create_task(runtime.begin_playback(item.id))
        await _until(lambda: len(audio.starts) == 1)
        audio.procs[0].finish(0)
        await _until(lambda: len(audio.starts) >= 2)
        runtime._segment_started = time.monotonic() - 3.3
        _double_tap(runtime)
        await _until(lambda: len(audio.starts) >= 3)
        assert audio.starts[2][1] == 0
        assert "-ss" not in audio.commands[2]
        assert calls == ["Message from Mom"]
        audio.procs[-1].finish(0)
        await task

    asyncio.run(scenario())


def test_a_double_tap_during_the_introduction_skips_it_and_starts_at_zero(tmp_path):
    calls: list[str] = []
    runtime = _runtime(tmp_path, calls)
    item = _note(runtime)
    audio: _Audio = runtime.audio

    async def scenario():
        runtime._coord.playing_id = item.id
        task = asyncio.create_task(runtime.begin_playback(item.id))
        await _until(lambda: len(audio.starts) == 1)
        _double_tap(runtime)
        await _until(lambda: len(audio.starts) >= 2)
        assert audio.starts[1] == (item.path.name, 0.0)
        assert calls == ["Message from Mom"]
        assert len([start for start in audio.starts if start[0] != item.path.name]) == 1
        audio.procs[-1].finish(0)
        await task

    asyncio.run(scenario())
