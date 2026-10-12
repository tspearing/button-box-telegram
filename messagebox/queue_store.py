"""On-disk FIFO queues for incoming and outgoing voice notes.

Files are written to a temporary name and renamed into place, then the
directory is fsynced. A power cut can lose the note being written. It
should not tear an older note or forget the order.

Each item is ``<id>.json`` plus a media file. Ids are a monotonic counter,
so oldest is always first even if the clock jumps.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
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
