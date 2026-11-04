"""Short spoken lines around a voice note.

A received note can be introduced as "Message from <name>". A successful
send can be followed by "Sent". Both use the same speech cache as card
names, so a repeated name is not synthesised again.
"""

from __future__ import annotations

from typing import Any, Mapping

from messagebox.announce import speakable_title

SENDER_FALLBACK = "Message"


def sender_intro(
    meta: Mapping[str, Any] | None,
    *,
    enabled: bool,
    overrides: Mapping[int, str],
) -> str | None:
    """The line to say before a received note, or None when introductions are off."""
    if not enabled:
        return None
    name = _sender_name(meta or {}, overrides)
    if not name:
        return SENDER_FALLBACK
    return f"Message from {name}"


def _sender_name(meta: Mapping[str, Any], overrides: Mapping[int, str]) -> str:
    user_id = _user_id(meta.get("sender_id"))
    if user_id is not None:
        override = overrides.get(user_id)
        if override:
            spoken = speakable_title(override)
            if spoken:
                return spoken
    raw = meta.get("sender_name") or meta.get("sender_label") or ""
    return speakable_title(str(raw)) if raw else ""


def _user_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def startup_cues(*, confirm_sent: bool, sent_text: str, not_sent_text: str, announce_sender: bool) -> list[str]:
    """Phrases worth speaking once at boot so they are ready when needed."""
    phrases: list[str] = []
    if confirm_sent:
        if sent_text:
            phrases.append(sent_text)
        if not_sent_text:
            phrases.append(not_sent_text)
    if announce_sender:
        phrases.append(SENDER_FALLBACK)
    return phrases
