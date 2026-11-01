"""On-disk FIFO queues for incoming and outgoing voice notes.

Files are written to a temporary name and renamed into place, then the
directory is fsynced. A power cut can lose the note being written. It
should not tear an older note or forget the order.

Each item is ``<id>.json`` plus a media file. Ids are a monotonic counter,
so oldest is always first even if the clock jumps.

The inbox is still one directory. Each note remembers the chat it came
from (``source_chat_id``). A note stays until it has been heard. After
that, only the latest heard note for that chat is kept, so a later press
can play it again. Older heard notes for that chat are deleted.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class QueuedItem:
    id: str
    path: Path
    meta: dict[str, Any]


class DiskQueue:
    """A durable FIFO directory of audio files."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self.list_items())

    def enqueue(self, source: Path, meta: dict[str, Any] | None = None) -> QueuedItem:
        """Copy ``source`` into the queue and return the new item."""
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        suffix = source.suffix.lower() or ".ogg"
        with self._lock:
            item_id = self._alloc_id()
            filename = f"{item_id}{suffix}"
            dest = self.root / filename
            partial = self.root / f".{item_id}.partial"
            self._copy_atomic(source, partial, dest)
            stored = {
                **(meta or {}),
                "id": item_id,
                "filename": filename,
            }
            self._write_json(item_id, stored)
            self._fsync_dir()
            return QueuedItem(id=item_id, path=dest, meta=stored)

    def enqueue_bytes(
        self,
        data: bytes,
        *,
        suffix: str = ".ogg",
        meta: dict[str, Any] | None = None,
    ) -> QueuedItem:
        """Enqueue audio that is already in memory (tests and small cues)."""
        if not suffix.startswith("."):
            suffix = "." + suffix
        with self._lock:
            item_id = self._alloc_id()
            filename = f"{item_id}{suffix}"
            dest = self.root / filename
            partial = self.root / f".{item_id}.partial"
            self._write_bytes_atomic(data, partial, dest)
            stored = {**(meta or {}), "id": item_id, "filename": filename}
            self._write_json(item_id, stored)
            self._fsync_dir()
            return QueuedItem(id=item_id, path=dest, meta=stored)

    def list_items(self) -> list[QueuedItem]:
        with self._lock:
            return self._list_unlocked()

    def peek(self) -> QueuedItem | None:
        """Oldest item, or None when the queue is empty."""
        items = self.list_items()
        return items[0] if items else None

    def head_state(self, now: float) -> tuple[str, QueuedItem | None]:
        """Look at the oldest item without skipping ahead.

        Returns ``("empty", None)``, ``("wait", item)`` when that item's
        ``next_attempt_at`` is still in the future, or ``("ready", item)``.
        A later item is never sent while an older one is waiting to retry.
        """
        item = self.peek()
        if item is None:
            return ("empty", None)
        due = float(item.meta.get("next_attempt_at", 0) or 0)
        if due > now:
            return ("wait", item)
        return ("ready", item)

    def get(self, item_id: str) -> QueuedItem | None:
        for item in self.list_items():
            if item.id == item_id:
                return item
        return None

    def update(self, item_id: str, **fields: Any) -> QueuedItem:
        with self._lock:
            current = self._get_unlocked(item_id)
            if current is None:
                raise KeyError(item_id)
            meta = dict(current.meta)
            meta.update(fields)
            meta["id"] = item_id
            self._write_json(item_id, meta)
            self._fsync_dir()
            return QueuedItem(id=item_id, path=current.path, meta=meta)

    def remove(self, item_id: str) -> None:
        """Delete an item. Missing items are ignored."""
        with self._lock:
            self._remove_unlocked(item_id)
            self._fsync_dir()

    def _list_unlocked(self) -> list[QueuedItem]:
        items: list[QueuedItem] = []
        if not self.root.exists():
            return items
        paths = sorted(self.root.glob("*.json"), key=lambda path: path.stem)
        for json_path in paths:
            item_id = json_path.stem
            if not item_id.isdigit():
                continue
            try:
                meta = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(meta, dict):
                continue
            media = self._media_for(item_id, meta)
            if media is None:
                continue
            items.append(QueuedItem(id=item_id, path=media, meta=meta))
        return items

    def _get_unlocked(self, item_id: str) -> QueuedItem | None:
        for item in self._list_unlocked():
            if item.id == item_id:
                return item
        return None

    def _media_for(self, item_id: str, meta: dict[str, Any]) -> Path | None:
        filename = meta.get("filename")
        if isinstance(filename, str) and filename:
            candidate = self.root / filename
            if candidate.is_file() and not candidate.name.startswith("."):
                return candidate
        matches = [
            path
            for path in self.root.glob(f"{item_id}.*")
            if path.suffix != ".json" and not path.name.startswith(".")
        ]
        if not matches:
            return None
        return sorted(matches)[0]

    def _remove_unlocked(self, item_id: str) -> None:
        if not item_id.isdigit():
            return
        for path in self.root.glob(f"{item_id}.*"):
            if path.name.startswith("."):
                continue
            path.unlink(missing_ok=True)
        partial = self.root / f".{item_id}.partial"
        partial.unlink(missing_ok=True)

    def _alloc_id(self) -> str:
        seq_path = self.root / ".seq"
        current = 0
        if seq_path.is_file():
            raw = seq_path.read_text(encoding="utf-8").strip()
            if raw.isdigit():
                current = int(raw)
        nxt = current + 1
        partial = self.root / ".seq.partial"
        partial.write_text(f"{nxt}\n", encoding="utf-8")
        partial.replace(seq_path)
        return f"{nxt:08d}"

    def _write_json(self, item_id: str, meta: dict[str, Any]) -> None:
        dest = self.root / f"{item_id}.json"
        partial = self.root / f".{item_id}.json.partial"
        payload = json.dumps(meta, ensure_ascii=False, sort_keys=True)
        self._write_bytes_atomic(payload.encode("utf-8"), partial, dest)

    def _copy_atomic(self, source: Path, partial: Path, dest: Path) -> None:
        with source.open("rb") as src, partial.open("wb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        partial.replace(dest)

    def _write_bytes_atomic(self, data: bytes, partial: Path, dest: Path) -> None:
        with partial.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        partial.replace(dest)

    def _fsync_dir(self) -> None:
        fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@dataclass(frozen=True)
class PlayChoice:
    """What a short press should do for one chat."""

    item: QueuedItem
    replay: bool


class Inbox:
    """Incoming notes for every chat, stored in one :class:`DiskQueue`.

    Unheard notes stay queued. Hearing one marks it and deletes any older
    heard note for the same chat, so disk use stays at one replay per chat.
    Notes that already have ``source_chat_id`` keep that chat. Notes saved
    before a chat id was stored can be attributed to the default chat.
    """

    def __init__(self, root: Path) -> None:
        self.queue = DiskQueue(root)

    def __len__(self) -> int:
        """Unheard notes across every chat. A retained replay is not waiting."""
        return self.waiting_count()

    def enqueue(self, source: Path, meta: dict[str, Any] | None = None) -> QueuedItem:
        return self.queue.enqueue(source, _incoming_meta(meta))

    def enqueue_bytes(
        self,
        data: bytes,
        *,
        suffix: str = ".ogg",
        meta: dict[str, Any] | None = None,
    ) -> QueuedItem:
        return self.queue.enqueue_bytes(data, suffix=suffix, meta=_incoming_meta(meta))

    def list_items(self) -> list[QueuedItem]:
        return self.queue.list_items()

    def get(self, item_id: str) -> QueuedItem | None:
        return self.queue.get(item_id)

    def remove(self, item_id: str) -> None:
        self.queue.remove(item_id)

    def waiting_count(self, chat_id: int | None = None) -> int:
        if chat_id is None:
            return sum(1 for item in self.queue.list_items() if not is_heard(item.meta))
        return sum(1 for item in self._matching(chat_id) if not is_heard(item.meta))

    def has_unheard(self, chat_id: int) -> bool:
        return self.waiting_count(chat_id) > 0

    def has_unheard_elsewhere(self, chat_id: int) -> bool:
        """True when some other chat, or an unattributed note, is still unheard."""
        try:
            focused = int(chat_id)
        except (TypeError, ValueError):
            focused = 0
        for item in self.queue.list_items():
            if is_heard(item.meta):
                continue
            other = source_chat_id(item.meta)
            if other is None or other != focused:
                return True
        return False

    def next_play(self, chat_id: int) -> PlayChoice | None:
        """Oldest unheard note for this chat, or its latest heard note."""
        pending = [item for item in self._matching(chat_id) if not is_heard(item.meta)]
        if pending:
            return PlayChoice(item=pending[0], replay=False)
        heard = [item for item in self._matching(chat_id) if is_heard(item.meta)]
        if not heard:
            return None
        return PlayChoice(item=max(heard, key=_heard_rank), replay=True)

    def mark_heard(self, item_id: str) -> None:
        """Keep this note for replay and drop older heard notes for its chat.

        ``heard_seq`` is stored on the note so the one just heard stays the
        latest even when two marks share a timestamp, including after a restart.
        """
        if self.queue.get(item_id) is None:
            return
        seq = 0
        for item in self.queue.list_items():
            raw = item.meta.get("heard_seq") or 0
            try:
                seq = max(seq, int(raw))
            except (TypeError, ValueError):
                continue
        self.queue.update(
            item_id,
            heard=True,
            heard_at=time.time(),
            heard_seq=seq + 1,
        )
        self.tidy_heard()

    def prepare(self, default_chat_id: int | None = None) -> int:
        """Attribute old notes that have no chat id, then bound heard notes.

        Returns how many notes gained a chat id. Notes that already store
        one are left on that chat. Nothing unheard is deleted.
        """
        changed = self.attribute_legacy(default_chat_id)
        self.tidy_heard()
        return changed

    def attribute_legacy(self, default_chat_id: int | None) -> int:
        """Give notes with no ``source_chat_id`` to the default chat.

        A missing or zero id does nothing: those notes stay on disk.
        """
        default = _optional_chat_id(default_chat_id)
        if default is None:
            return 0
        changed = 0
        for item in self.queue.list_items():
            if source_chat_id(item.meta) is not None:
                continue
            self.queue.update(item.id, source_chat_id=default)
            changed += 1
        return changed

    def tidy_heard(self) -> None:
        """Leave one heard note per chat: the one heard most recently."""
        grouped: dict[int, list[QueuedItem]] = {}
        for item in self.queue.list_items():
            if not is_heard(item.meta):
                continue
            chat = source_chat_id(item.meta)
            if chat is None:
                continue
            grouped.setdefault(chat, []).append(item)
        for items in grouped.values():
            if len(items) < 2:
                continue
            keep = max(items, key=_heard_rank)
            for item in items:
                if item.id != keep.id:
                    self.queue.remove(item.id)

    def _matching(self, chat_id: int) -> list[QueuedItem]:
        wanted = _optional_chat_id(chat_id)
        if wanted is None:
            return []
        return [item for item in self.queue.list_items() if source_chat_id(item.meta) == wanted]


def source_chat_id(meta: dict[str, Any]) -> int | None:
    """Chat id stored with a note, or None when the file predates one."""
    raw = meta.get("source_chat_id")
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def is_heard(meta: dict[str, Any]) -> bool:
    """True only when this process has marked the note heard.

    A missing flag means the note is still waiting. That is how notes
    saved before this behaviour are treated.
    """
    return meta.get("heard") is True


def _incoming_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    stored = dict(meta or {})
    stored.setdefault("heard", False)
    return stored


def _optional_chat_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        chat_id = int(value)
    except (TypeError, ValueError):
        return None
    if chat_id == 0:
        return None
    return chat_id


def _heard_rank(item: QueuedItem) -> tuple[int, float, str]:
    raw_seq = item.meta.get("heard_seq") or 0
    try:
        seq = int(raw_seq)
    except (TypeError, ValueError):
        seq = 0
    raw = item.meta.get("heard_at") or 0
    try:
        stamp = float(raw)
    except (TypeError, ValueError):
        stamp = 0.0
    return (seq, stamp, item.id)
