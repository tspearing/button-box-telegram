"""The steady lamp ends when recording ends, including a microphone failure."""

import asyncio

from messagebox.button_machine import ButtonMachine
from messagebox.cards import build_directory
from messagebox.config import AppConfig, PathsConfig
from messagebox.coordinator import LAMP_OFF, LAMP_RECORDING, LAMP_WAITING, Coordinator
from messagebox.queue_store import DiskQueue, Inbox
from messagebox.runtime import Runtime
from messagebox.sounds import SoundBank

CHAT = -1000000000001


class _Audio:
    def __init__(self, *, start_error: BaseException | None = None, stop_error: BaseException | None = None):
        self.start_error = start_error
        self.stop_error = stop_error
        self.played: list[str] = []

    async def play_and_wait(self, path) -> bool:
        self.played.append(path.name)
        return True

    async def start_recording(self, output, *, max_seconds: float):
        if self.start_error is not None:
            raise self.start_error
        return _Proc(None)

    async def stop_recording(self, proc, output) -> bool:
        if self.stop_error is not None:
            raise self.stop_error
        return False


class _Proc:
    def __init__(self, returncode):
        self.returncode = returncode


def _runtime(tmp_path, audio: _Audio):
    runtime = Runtime(AppConfig(paths=PathsConfig(data_dir=tmp_path)))
    runtime.audio = audio
    runtime._sounds = SoundBank(tmp_path / "sounds")
    runtime._sounds.ensure()
    inbox = Inbox(tmp_path / "inbox")
    inbox.enqueue_bytes(b"waiting", meta={"source_chat_id": CHAT})
    runtime._coord = Coordinator(
        ButtonMachine(hold_seconds=1.0, min_record_seconds=1.0, max_record_seconds=5.0),
        inbox,
        focused_chat=lambda: CHAT,
    )
    runtime._coord.boot()
    runtime._outbox = DiskQueue(tmp_path / "outbox")
    runtime._directory = build_directory(CHAT, (), 120)
    return runtime


def test_microphone_failing_to_start_turns_the_steady_lamp_off(tmp_path):
    runtime = _runtime(tmp_path, _Audio(start_error=OSError("microphone unavailable")))
    runtime._coord.mic_opened()
    assert runtime._coord.lamp_mode() == LAMP_RECORDING

    asyncio.run(runtime.begin_capture())

    assert runtime._coord.mic_open is False
    assert runtime._coord.lamp_mode() == LAMP_WAITING
    assert runtime._capturing is False


def test_microphone_dying_mid_recording_drops_the_lamp_while_the_button_is_held(tmp_path):
    runtime = _runtime(tmp_path, _Audio(stop_error=RuntimeError("microphone failed")))
    coord = runtime._coord
    coord.press(0)
    coord.tick(1)
    coord.mic_opened()
    assert coord.lamp_mode() == LAMP_RECORDING
    assert coord.recording

    runtime._capturing = True
    runtime._capture_proc = _Proc(1)
    runtime._capture_path = tmp_path / "live.ogg"
    runtime._capture_path.write_bytes(b"x")

    async def recover_and_finish():
        await runtime._recover_failed_capture()
        assert coord.recording
        assert coord.mic_open is False
        assert coord.lamp_mode() == LAMP_WAITING
        job = runtime._jobs.get_nowait()
        await job()

    asyncio.run(recover_and_finish())

    assert coord.recording
    assert coord.mic_open is False
    assert coord.lamp_mode() == LAMP_WAITING
    assert coord.lamp_mode() != LAMP_OFF
