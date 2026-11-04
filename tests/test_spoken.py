"""Sender introductions and the spoken sent confirmation.

Placeholder ids only: 123456789 is Mom, 234567890 is Dad, 345678901 is Grandma.
The chat id -1000000000001 is a stand-in group.
"""

import asyncio
from dataclasses import replace
from pathlib import Path

from messagebox.announce import Announcer
from messagebox.audio import AudioEngine
from messagebox.button_machine import ButtonMachine
from messagebox.cards import build_directory
from messagebox.config import AnnounceConfig, AppConfig, AudioConfig, PathsConfig, TelegramConfig
from messagebox.coordinator import Coordinator
from messagebox.cues import sender_intro
from messagebox.queue_store import DiskQueue, Inbox, is_heard
from messagebox.runtime import Runtime
from messagebox.sounds import SoundBank
from messagebox.telegram_bridge import TelegramBridge

CHAT = -1000000000001
MOM = 123456789
DAD = 234567890
GRANDMA = 345678901


class _Proc:
    def __init__(self) -> None:
        self.returncode = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = -15


class RecordingAudio(AudioEngine):
    """Records play() without opening a speaker. play() is the lead-in path."""

    def __init__(self) -> None:
        super().__init__(
            AudioConfig(
                speaker_device="plughw:2,0",
                lead_in_mode="noise",
                lead_in_ms=300,
                lead_in_level_db=-60,
            )
        )
        self.played: list[Path] = []
        self.commands: list[list[str]] = []
        self.on_play = None

    async def play(self, path: Path):
        self.commands.append(self.play_cmd(path))
        self.played.append(Path(path))
        if self.on_play is not None:
            self.on_play(path)
        return _Proc()

    async def play_and_wait(self, path: Path) -> bool:
        self.commands.append(self.play_cmd(path))
        self.played.append(Path(path))
        return True


def _speech(cache: Path, calls: list[str]) -> Announcer:
    def synth(text: str, dest: Path) -> bool:
        calls.append(text)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"RIFF" + b"\x00" * 80)
        return True

    return Announcer(cache, synth=synth)


def _runtime(tmp_path: Path, calls: list[str], **announce: object) -> Runtime:
    config = AppConfig(
        paths=PathsConfig(data_dir=tmp_path),
        telegram=TelegramConfig(allowed_chat_ids=(CHAT,), family_chat_id=CHAT),
        announce=AnnounceConfig(**announce),
    )
    runtime = Runtime(config)
    runtime.audio = RecordingAudio()
    runtime._sounds = SoundBank(tmp_path / "sounds")
    runtime._sounds.ensure()
    runtime._directory = build_directory(CHAT, (), 120)
    runtime._outbox = DiskQueue(tmp_path / "outbox")
    runtime._inbox = Inbox(tmp_path / "inbox")
    runtime._coord = Coordinator(ButtonMachine(), runtime._inbox, focused_chat=lambda: CHAT)
    runtime._announcer = _speech(tmp_path / "cache", calls)
    return runtime


def _note(runtime: Runtime, sender_id: int, name: str, *, label: str | None = None):
    assert runtime._inbox is not None
    return runtime._inbox.enqueue_bytes(
        b"OggS-note",
        meta={
            "source_chat_id": CHAT,
            "sender_id": sender_id,
            "sender_name": name,
            "sender_label": name if label is None else label,
        },
    )


class _File:
    file_path = "note.ogg"

    async def download_to_drive(self, custom_path: str) -> None:
        Path(custom_path).write_bytes(b"OggS-incoming")


class _Voice:
    duration = 3

    async def get_file(self) -> _File:
        return _File()


class _Message:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id
        self.voice = _Voice()
        self.audio = None
        self.document = None


class _Chat:
    def __init__(self, chat_id: int, title: str = "Family") -> None:
        self.id = chat_id
        self.title = title


class _User:
    def __init__(self, user_id: int, first_name: str | None, username: str | None = None, *, is_bot: bool = False) -> None:
        self.id = user_id
        self.first_name = first_name
        self.username = username
        self.is_bot = is_bot


class _Update:
    def __init__(self, message, chat, user) -> None:
        self.effective_message = message
        self.effective_chat = chat
        self.effective_user = user


def test_incoming_note_stores_the_sender_and_keeps_it(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    seen: list[dict] = []

    async def queued() -> None:
        return None

    async def incoming(meta: dict) -> None:
        seen.append(dict(meta))

    bridge = TelegramBridge(
        token="123456:TEST",
        allowed={CHAT},
        inbox=inbox,
        on_queued=queued,
        download_dir=tmp_path / "tmp",
        on_incoming_meta=incoming,
    )

    async def deliver() -> None:
        await bridge.on_media(
            _Update(_Message(1), _Chat(CHAT), _User(MOM, "Mom", "mom_example")),
            None,
        )
        await bridge.on_media(
            _Update(_Message(2), _Chat(CHAT), _User(DAD, None, "dad_example")),
            None,
        )
        await bridge.on_media(
            _Update(_Message(3), _Chat(CHAT), _User(GRANDMA, "", "grandma_example")),
            None,
        )
        await bridge.on_media(
            _Update(_Message(4), _Chat(CHAT), _User(1, "Bot", is_bot=True)),
            None,
        )
        await bridge.on_media(
            _Update(_Message(5), _Chat(-1000000000002), _User(MOM, "Mom")),
            None,
        )

    asyncio.run(deliver())

    reopened = Inbox(tmp_path / "inbox")
    items = reopened.queue.list_items()
    assert [(item.meta["sender_id"], item.meta["sender_name"]) for item in items] == [
        (MOM, "Mom"),
        (DAD, "dad_example"),
        (GRANDMA, "grandma_example"),
    ]
    assert items[0].meta["sender_label"] == "Mom"
    assert [sender_intro(item.meta, enabled=True, overrides={}) for item in items] == [
        "Message from Mom",
        "Message from dad example",
        "Message from grandma example",
    ]
    assert len(seen) == 3
    older = Inbox(tmp_path / "older")
    kept = older.enqueue_bytes(b"old", meta={"source_chat_id": CHAT, "sender_label": "Grandma"})
    again = Inbox(tmp_path / "older")
    restored = again.get(kept.id)
    assert restored is not None
    assert sender_intro(restored.meta, enabled=True, overrides={}) == "Message from Grandma"


def test_playback_says_the_sender_then_the_note_with_a_noise_lead_in(tmp_path):
    calls: list[str] = []
    runtime = _runtime(tmp_path, calls)
    item = _note(runtime, MOM, "Mom")
    assert runtime._coord is not None
    runtime._coord.playing_id = item.id
    phrase = "Message from Mom"
    cached = runtime._announcer.clip_for(phrase)
    calls.clear()

    asyncio.run(runtime.begin_playback(item.id))

    assert calls == []
    assert runtime.audio.played[0] == cached
    assert runtime.audio.played[1] == item.path
    assert all("anoisesrc" in " ".join(command) for command in runtime.audio.commands)
    restored = runtime._inbox.get(item.id)
    assert restored is not None
    assert is_heard(restored.meta)


def test_the_same_spoken_name_is_synthesised_once(tmp_path):
    calls: list[str] = []
    announcer = _speech(tmp_path / "cache", calls)
    mom = sender_intro({"sender_id": MOM, "sender_name": "Mom"}, enabled=True, overrides={})
    again = sender_intro({"sender_id": DAD, "sender_name": "Mom"}, enabled=True, overrides={})
    dad = sender_intro({"sender_id": DAD, "sender_name": "Dad"}, enabled=True, overrides={})
    first = announcer.clip_for(mom)
    second = announcer.clip_for(again)
    announcer.clip_for(dad)
    announcer.clip_for("Sent")
    announcer.clip_for("Sent")
    assert first == second
    assert calls == ["Message from Mom", "Message from Dad", "Sent"]


def test_interrupting_the_introduction_leaves_the_note_unheard(tmp_path):
    calls: list[str] = []
    runtime = _runtime(tmp_path, calls)
    item = _note(runtime, MOM, "Mom")
    runtime._coord.playing_id = item.id

    def interrupt(_path) -> None:
        runtime._interrupt_playback()

    runtime.audio.on_play = interrupt
    asyncio.run(runtime.begin_playback(item.id))

    assert len(runtime.audio.played) == 1
    restored = runtime._inbox.get(item.id)
    assert restored is not None
    assert is_heard(restored.meta) is False


def test_sender_announcement_can_be_switched_off(tmp_path):
    calls: list[str] = []
    runtime = _runtime(tmp_path, calls, announce_sender=False)
    item = _note(runtime, MOM, "Mom")
    runtime._coord.playing_id = item.id

    asyncio.run(runtime.begin_playback(item.id))

    assert calls == []
    assert runtime.audio.played == [item.path]


def test_a_config_name_replaces_the_telegram_name(tmp_path):
    calls: list[str] = []
    runtime = _runtime(tmp_path, calls)
    runtime.config = replace(runtime.config, senders={MOM: "Grandma"})
    item = _note(runtime, MOM, "Someone")
    runtime._coord.playing_id = item.id

    asyncio.run(runtime.begin_playback(item.id))

    assert calls == ["Message from Grandma"]


def test_startup_prepares_sent_and_saved_sender_names(tmp_path):
    calls: list[str] = []
    runtime = _runtime(tmp_path, calls)
    _note(runtime, MOM, "Mom")
    _note(runtime, MOM, "Mom")
    _note(runtime, DAD, "Dad")

    async def warm() -> None:
        runtime._warm_announcements()
        await asyncio.gather(*list(runtime._background))

    asyncio.run(warm())

    assert calls == ["Sent", "Not sent", "Message", "Message from Mom", "Message from Dad"]


def test_sent_is_spoken_only_after_a_successful_upload(tmp_path):
    calls: list[str] = []
    runtime = _runtime(tmp_path, calls)
    _enqueue_outgoing(runtime, "one")
    _enqueue_outgoing(runtime, "two")

    async def send(_chat_id, _item) -> None:
        if runtime._outbox is not None and len(runtime._outbox) == 1:
            runtime._stop.set()

    async def run() -> None:
        runtime._warm_announcements()
        await asyncio.gather(*list(runtime._background))
        sent = runtime._announcer.clip_for("Sent")
        calls.clear()
        runtime.audio.played.clear()
        runtime.audio.commands.clear()
        runtime._send = send
        await runtime._outbox_loop()
        if runtime._background:
            await asyncio.gather(*list(runtime._background))
        return sent

    sent = asyncio.run(run())

    assert calls == []
    assert runtime.audio.played == [sent, sent]
    assert all("anoisesrc" in " ".join(command) for command in runtime.audio.commands)
    assert runtime._outbox is not None
    assert len(runtime._outbox) == 0


def test_the_first_send_failure_says_not_sent_and_a_retry_stays_quiet(tmp_path):
    calls: list[str] = []
    runtime = _runtime(tmp_path, calls)
    _enqueue_outgoing(runtime, "one")

    async def send(_chat_id, _item) -> None:
        runtime._stop.set()
        raise ConnectionError("offline")

    async def run() -> None:
        runtime._send = send
        await runtime._outbox_loop()
        if runtime._background:
            await asyncio.gather(*list(runtime._background))

    asyncio.run(run())
    assert calls == ["Not sent"]
    assert runtime._outbox.peek().meta["attempts"] == 1

    calls.clear()
    runtime.audio.played.clear()
    runtime._stop.clear()
    waiting = runtime._outbox.peek()
    assert waiting is not None
    runtime._outbox.update(waiting.id, next_attempt_at=0)

    async def retry(_chat_id, _item) -> None:
        runtime._stop.set()
        raise ConnectionError("offline")

    async def run_retry() -> None:
        runtime._send = retry
        await runtime._outbox_loop()
        if runtime._background:
            await asyncio.gather(*list(runtime._background))

    asyncio.run(run_retry())
    assert calls == []
    assert runtime.audio.played == []
    assert runtime._outbox.peek().meta["attempts"] == 2


def test_an_empty_failure_phrase_stays_quiet(tmp_path):
    calls: list[str] = []
    runtime = _runtime(tmp_path, calls, not_sent_text="")
    _enqueue_outgoing(runtime, "one")

    async def send(_chat_id, _item) -> None:
        runtime._stop.set()
        raise ConnectionError("offline")

    async def run() -> None:
        runtime._send = send
        await runtime._outbox_loop()
        if runtime._background:
            await asyncio.gather(*list(runtime._background))

    asyncio.run(run())
    assert calls == []
    assert runtime.audio.played == []


def test_confirm_sent_off_keeps_the_chime(tmp_path):
    calls: list[str] = []
    runtime = _runtime(tmp_path, calls, confirm_sent=False)
    _enqueue_outgoing(runtime, "one")

    async def send(_chat_id, _item) -> None:
        runtime._stop.set()

    async def run() -> None:
        runtime._send = send
        await runtime._outbox_loop()
        if runtime._background:
            await asyncio.gather(*list(runtime._background))

    asyncio.run(run())
    assert calls == []
    assert [path.name for path in runtime.audio.played] == ["sent.wav"]


def _enqueue_outgoing(runtime: Runtime, name: str) -> None:
    assert runtime._outbox is not None
    source = runtime.config.paths.data_dir / f"{name}.ogg"
    source.write_bytes(b"OggS-outgoing")
    runtime._outbox.enqueue(source, {"target_chat_id": CHAT, "duration": 2, "attempts": 0})
