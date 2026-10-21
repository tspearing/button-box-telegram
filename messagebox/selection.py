"""Remember the last card across a restart.

The file lives in the data directory (``/var/lib/message-box/selection.json``
on the Pi). A missing or unreadable file means "use the default chat".
Restoring a selection does not speak. Only a tap does.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from messagebox.recipients import normalize_uid

log = logging.getLogger("messagebox.selection")


def load_selection(path: Path) -> tuple[str, int] | None:
    """Return ``(uid, chat_id)`` or None."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        uid = normalize_uid(str(data["uid"]))
        chat_id = int(data["chat_id"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        log.warning("ignoring unreadable selection file %s", path)
        return None
    return uid, chat_id


def save_selection(path: Path, *, chat_id: int, uid: str) -> None:
    """Atomic replace. The caller logs and continues if this raises ``OSError``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"chat_id": int(chat_id), "uid": normalize_uid(uid)}, indent=2) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
