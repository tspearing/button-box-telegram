"""Unauthorized Telegram updates must not download, queue, or speak.

Placeholder ids only. -1000000000001 is an allowed group. 123456789 is an
approved sender. Nothing here is a real account.
"""

import asyncio
from pathlib import Path

from messagebox.queue_store import Inbox
from messagebox.telegram_bridge import TelegramBridge, safe_incoming_path

CHAT = -1000000000001
OTHER = -1000000000002
MOM = 123456789
DAD = 234567890
STRANGER = 345678901


class _File:
    def __init__(self, file_path: str = "voice/note.ogg") -> None:
        self.file_path = file_path
        self.downloads: list[str] = []

    async def download_to_drive(self, custom_path: str) -> None:
        self.downloads.append(custom_path)
        Path(custom_path).write_bytes(b"OggS-incoming-bytes")


class _Voice:
    def __init__(self, *, file_size: int | None = 800, duration: object = 3, file_path: str = "voice/note.ogg") -> None:
        self.file_size = file_size
        self.duration = duration
        self.file = _File(file_path)
        self.fetched = 0

    async def get_file(self) -> _File:
        self.fetched += 1
        return self.file


class _User:
    def __init__(self, user_id, first_name="Mom", *, is_bot=False) -> None:
        self.id = user_id
        self.first_name = first_name
        self.username = None
        self.is_bot = is_bot


class _Chat:
    def __init__(self, chat_id, title="Family") -> None:
        self.id = chat_id
        self.title = title


class _Message:
    def __init__(self, message_id, user, voice: _Voice | None, **extra) -> None:
        self.message_id = message_id
        self.from_user = user
        self.voice = voice
        self.audio = None
        self.document = None
        for key, value in extra.items():
            setattr(self, key, value)


class _Update:
    def __init__(self, message, chat, effective_user=None) -> None:
        self.effective_message = message
        self.effective_chat = chat
        self.effective_user = effective_user


def _bridge(tmp_path, **kwargs):
    queued: list[int] = []
    metas: list[dict] = []
    titles: list[tuple[int, str]] = []

    async def on_queued() -> None:
        queued.append(1)

    async def on_meta(meta: dict) -> None:
        metas.append(meta)

    async def on_title(chat_id: int, title: str) -> None:
        titles.append((chat_id, title))

    bridge = TelegramBridge(
        token="123456:TEST",
        allowed=kwargs.pop("allowed", {CHAT}),
        allowed_senders=kwargs.pop("allowed_senders", {MOM}),
        allow_any_sender=kwargs.pop("allow_any_sender", False),
        inbox=Inbox(tmp_path / "inbox"),
        on_queued=on_queued,
        download_dir=tmp_path / "downloads",
        on_incoming_meta=on_meta,
        on_title=on_title,
        **kwargs,
    )
    return bridge, queued, metas, titles


def _send(bridge, message, chat, effective_user=None) -> None:
    asyncio.run(bridge.on_media(_Update(message, chat, effective_user), None))


def test_an_approved_voice_note_is_saved_once(tmp_path):
    bridge, queued, metas, titles = _bridge(tmp_path)
    voice = _Voice()
    mom = _User(MOM)
    _send(bridge, _Message(7, mom, voice), _Chat(CHAT, "Family"))
    assert voice.fetched == 1
    assert voice.file.downloads
    assert Path(voice.file.downloads[0]).parent == (tmp_path / "downloads").resolve()
    assert len(bridge.inbox) == 1 and len(queued) == 1 and len(metas) == 1
    assert metas[0]["sender_id"] == MOM
    assert titles == [(CHAT, "Family")]
    # The Telegram path is not left on disk and was not the local filename.
    assert not Path(voice.file.downloads[0]).exists()
    stored = bridge.inbox.list_items()[0]
    assert stored.path.name.endswith(".ogg")
    assert "voice" not in stored.path.name


def test_unauthorized_chat_does_not_download_or_touch_the_box(tmp_path):
    bridge, queued, metas, titles = _bridge(tmp_path)
    voice = _Voice(file_path="../../etc/passwd.ogg")
    _send(bridge, _Message(1, _User(MOM), voice), _Chat(OTHER))
    assert voice.fetched == 0
    assert queued == [] and metas == [] and titles == []
    assert len(bridge.inbox) == 0


def test_unauthorized_sender_in_an_allowed_chat_is_ignored(tmp_path):
    bridge, queued, metas, titles = _bridge(tmp_path)
    voice = _Voice()
    _send(bridge, _Message(2, _User(STRANGER, "Stranger"), voice), _Chat(CHAT))
    asyncio.run(bridge.on_seen(_Update(_Message(3, _User(STRANGER), None), _Chat(CHAT)), None))
    assert voice.fetched == 0
    assert queued == [] and metas == [] and titles == []


def test_an_approved_sender_does_not_open_a_new_chat(tmp_path):
    bridge, queued, metas, titles = _bridge(tmp_path)
    voice = _Voice()
    private = _Chat(MOM)
    _send(bridge, _Message(4, _User(MOM), voice), private)
    assert voice.fetched == 0
    assert queued == [] and titles == []


def test_forward_metadata_and_missing_senders_are_not_identities(tmp_path):
    bridge, queued, metas, titles = _bridge(tmp_path)
    voice = _Voice()
    stranger = _User(STRANGER, "Stranger")
    spoofed = _Message(
        5,
        stranger,
        voice,
        forward_from=_User(MOM),
        forward_sender_name="Mom",
        sender_chat=_Chat(CHAT),
    )
    # effective_user is the approved person, but from_user is not.
    _send(bridge, spoofed, _Chat(CHAT), effective_user=_User(MOM))
    missing = _Message(6, None, _Voice())
    _send(bridge, missing, _Chat(CHAT))
    anonymous = _Message(8, _User(999999999, is_bot=True), _Voice(), sender_chat=_Chat(CHAT))
    _send(bridge, anonymous, _Chat(CHAT))
    assert voice.fetched == 0
    assert missing.voice.fetched == 0
    assert queued == [] and metas == [] and titles == []


def test_a_bad_message_id_is_not_used_as_a_path(tmp_path):
    bridge, queued, metas, _titles = _bridge(tmp_path)
    voice = _Voice(file_path="/tmp/not-a-local-file.ogg")
    outside = tmp_path / "outside.ogg"
    message = _Message("../../outside", _User(MOM), voice)
    _send(bridge, message, _Chat(CHAT))
    assert voice.fetched == 0
    assert not outside.exists()
    assert queued == [] and metas == []
    escaped = safe_incoming_path(tmp_path / "downloads", CHAT, -1, ".ogg")
    assert escaped is None


def test_oversized_or_unknown_size_is_not_downloaded(tmp_path):
    bridge, queued, _metas, _titles = _bridge(tmp_path, max_incoming_bytes=1000)
    huge = _Voice(file_size=50_000_000)
    _send(bridge, _Message(9, _User(MOM), huge), _Chat(CHAT))
    unknown = _Voice(file_size=None)
    _send(bridge, _Message(10, _User(MOM), unknown), _Chat(CHAT))
    too_long = _Voice(file_size=500, duration=10_000)
    _send(bridge, _Message(11, _User(MOM), too_long), _Chat(CHAT))
    assert huge.fetched == unknown.fetched == too_long.fetched == 0
    assert queued == []


def test_a_downloaded_file_larger_than_promised_is_dropped(tmp_path):
    bridge, queued, metas, _titles = _bridge(tmp_path, max_incoming_bytes=8)

    class _Fat(_File):
        async def download_to_drive(self, custom_path: str) -> None:
            Path(custom_path).write_bytes(b"0123456789abcdef")

    voice = _Voice(file_size=4)
    voice.file = _Fat()
    _send(bridge, _Message(12, _User(MOM), voice), _Chat(CHAT))
    assert voice.fetched == 1
    assert queued == [] and metas == []
    assert not any((tmp_path / "downloads").glob("incoming-*"))


def test_allow_any_sender_still_requires_the_chat_and_a_real_user(tmp_path):
    bridge, queued, _metas, titles = _bridge(tmp_path, allowed_senders=set(), allow_any_sender=True)
    voice = _Voice()
    _send(bridge, _Message(13, _User(STRANGER, "Stranger"), voice), _Chat(CHAT))
    assert voice.fetched == 1 and queued == [1]
    other = _Voice()
    _send(bridge, _Message(14, _User(STRANGER, "Stranger"), other), _Chat(OTHER))
    missing = _Message(15, None, _Voice())
    _send(bridge, missing, _Chat(CHAT))
    assert other.fetched == 0 and missing.voice.fetched == 0
    assert len(queued) == 1
    assert titles == [(CHAT, "Family")]


def test_a_malformed_update_does_nothing(tmp_path):
    bridge, queued, metas, titles = _bridge(tmp_path)

    class _Empty:
        effective_message = None
        effective_chat = None

    asyncio.run(bridge.on_media(_Empty(), None))
    asyncio.run(bridge.on_seen(_Empty(), None))
    assert queued == [] and metas == [] and titles == []
