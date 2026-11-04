"""A card says the Telegram chat title unless the config overrides it."""

import asyncio
from pathlib import Path

from messagebox.announce import Announcer
from messagebox.chat_titles import (
    SPOKEN_FALLBACK,
    ChatTitles,
    announcement_for,
    fetch_chat_title,
    speakable_title,
)
from messagebox.config import CardConfig

CHAT = -1001234567890


def _card(**kwargs) -> CardConfig:
    values = dict(uid="04A1B2C3D4E5F6", chat_id=CHAT, announce="")
    values.update(kwargs)
    return CardConfig(**values)


def test_speakable_title_drops_emoji_and_trims():
    assert speakable_title("Family chat 🎉") == "Family chat"
    assert speakable_title("A & B") == "A and B"
    assert speakable_title("!!!") == ""
    long = "word " * 40
    assert len(speakable_title(long)) <= 80
    assert speakable_title(long).endswith("word") or speakable_title(long).endswith("wor")


def test_priority_is_file_then_text_then_title_then_fallback(tmp_path):
    titles = ChatTitles(tmp_path / "titles.json")
    wav = tmp_path / "name.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 80)
    missing = tmp_path / "missing.wav"

    with_file = _card(announce="Short", announce_file=wav)
    assert announcement_for(with_file, titles) == ("Short", wav)

    text_only = _card(announce="Short", announce_file=missing)
    assert announcement_for(text_only, titles) == ("Short", missing)

    titles.remember(CHAT, "Family chat")
    from_title = _card()
    assert announcement_for(from_title, titles) == ("Family chat", None)

    # A configured word still wins after a title is known.
    assert announcement_for(text_only, titles)[0] == "Short"

    empty = ChatTitles(tmp_path / "empty.json")
    assert announcement_for(_card(), empty) == (SPOKEN_FALLBACK, None)


def test_titles_survive_a_restart_and_a_rename_is_new_text(tmp_path):
    path = tmp_path / "titles.json"
    titles = ChatTitles(path)
    assert titles.spoken(CHAT) is None
    assert titles.remember(CHAT, "Family chat") == "Family chat"
    assert titles.remember(CHAT, "Family chat") is None
    assert titles.remember(CHAT, "Family chat 🎉") is None

    again = ChatTitles(path)
    again.load()
    assert again.spoken(CHAT) == "Family chat"
    assert again.remember(CHAT, "Other room") == "Other room"

    calls: list[str] = []

    def synth(text: str, dest: Path) -> bool:
        calls.append(text)
        dest.write_bytes(b"RIFF" + text.encode("utf-8") + b"\x00" * 80)
        return True

    announcer = Announcer(tmp_path / "clips", synth)
    first = announcer.clip_for("Family chat")
    second = announcer.clip_for("Other room")
    assert first != second
    assert first is not None and second is not None
    assert calls == ["Family chat", "Other room"]
    assert announcer.clip_for("Other room") == second
    assert calls == ["Family chat", "Other room"]


def test_fetch_title_uses_get_chat_and_ignores_failures():
    class Chat:
        def __init__(self, title):
            self.title = title

    class Bot:
        def __init__(self, result):
            self.result = result
            self.seen = None

        async def get_chat(self, chat_id):
            self.seen = chat_id
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    bot = Bot(Chat("Family chat"))
    assert asyncio.run(fetch_chat_title(bot, CHAT)) == "Family chat"
    assert bot.seen == CHAT

    quiet = Bot(Chat(None))
    assert asyncio.run(fetch_chat_title(quiet, CHAT)) is None

    broken = Bot(ConnectionError("offline"))
    assert asyncio.run(fetch_chat_title(broken, CHAT)) is None


def test_broken_title_file_is_ignored(tmp_path):
    path = tmp_path / "titles.json"
    path.write_text("{", encoding="utf-8")
    titles = ChatTitles(path)
    titles.load()
    assert titles.spoken(CHAT) is None
    assert announcement_for(_card(), titles) == (SPOKEN_FALLBACK, None)
