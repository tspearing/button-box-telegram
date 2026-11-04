"""Remember Telegram chat titles so a card can say the group name offline.

The spoken name is resolved when a card is tapped:

1. a wav file, when ``announce_file`` is set and the file exists
2. ``announce`` text, when it is set
3. the cached chat title
4. the word "Chat"

Titles are fetched with ``getChat`` and also taken from messages the bot
already sees. The file is the cache, so a restart does not need the network
before the first tap.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from messagebox.announce import MAX_ANNOUNCE_CHARS, clean_announce_text
from messagebox.config import CardConfig

log = logging.getLogger("messagebox.chat_titles")

SPOKEN_FALLBACK = "Chat"
TITLE_REFRESH_SECONDS = 6 * 60 * 60


def speakable_title(raw: str) -> str:
    """A short phrase Piper or espeak-ng can say.

    Emoji and other symbols are dropped. ``&`` becomes "and". The result
    is trimmed to the same length limit as a configured announce line.
    """
    if not raw:
        return ""
    pieces: list[str] = []
    for char in str(raw):
        if char == "&":
            pieces.append(" and ")
        elif char.isalpha() or char.isdigit() or char in " '-":
            pieces.append(char)
        else:
            pieces.append(" ")
    spoken = clean_announce_text("".join(pieces))
    if len(spoken) > MAX_ANNOUNCE_CHARS:
        spoken = spoken[:MAX_ANNOUNCE_CHARS].rstrip()
    return spoken


class ChatTitles:
    """Spoken titles keyed by chat id, stored as JSON."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._spoken: dict[int, str] = {}
        self._lock = threading.Lock()

    def load(self) -> None:
        """Read the cache. A missing or broken file means no titles yet."""
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("ignoring unreadable chat title file %s", self.path)
            return
        if not isinstance(data, dict):
            return
        loaded: dict[int, str] = {}
        for key, value in data.items():
            try:
                chat_id = int(key)
            except (TypeError, ValueError):
                continue
            spoken = ""
            if isinstance(value, dict):
                spoken = speakable_title(str(value.get("spoken") or value.get("title") or ""))
            elif isinstance(value, str):
                spoken = speakable_title(value)
            if spoken:
                loaded[chat_id] = spoken
        with self._lock:
            self._spoken = loaded

    def spoken(self, chat_id: int) -> str | None:
        with self._lock:
            return self._spoken.get(int(chat_id))

    def remember(self, chat_id: int, raw_title: str) -> str | None:
        """Store a title. Return the spoken form only when it changed."""
        spoken = speakable_title(raw_title)
        if not spoken:
            return None
        chat_id = int(chat_id)
        with self._lock:
            if self._spoken.get(chat_id) == spoken:
                return None
            self._spoken[chat_id] = spoken
            snapshot = dict(self._spoken)
        self._save(snapshot)
        log.info("saved a chat title for %s", chat_id)
        return spoken

    def _save(self, spoken: dict[int, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {str(chat_id): {"spoken": text} for chat_id, text in sorted(spoken.items())}
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)


def announcement_for(card: CardConfig, titles: ChatTitles) -> tuple[str, Path | None]:
    """Words to say, and an optional wav that replaces them when the file exists."""
    if card.announce:
        spoken = card.announce
    else:
        spoken = titles.spoken(card.chat_id) or SPOKEN_FALLBACK
    return spoken, card.announce_file


async def fetch_chat_title(bot, chat_id: int) -> str | None:
    """Ask Telegram for the group title. None when the call fails or there is none."""
    try:
        chat = await bot.get_chat(int(chat_id))
    except Exception as exc:
        log.warning("could not read chat %s (%s)", chat_id, type(exc).__name__)
        return None
    title = getattr(chat, "title", None)
    if not title:
        return None
    return str(title)
