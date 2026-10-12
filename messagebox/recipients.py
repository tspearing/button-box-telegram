"""Who the box is talking to.

Version 1 has a single recipient: the family chat. Later, an NFC card can
call :meth:`RecipientDirectory.select_card` and the send path does not
change. After ``idle_timeout_seconds`` with no activity the directory
returns to the family chat. See ``docs/ROADMAP.md``.

This module does not read cards. Nothing in v1 constructs a card reader.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Recipient:
    """A person or group the box can send a voice note to."""

    key: str
    chat_id: int
    display_name: str
    # Future: a short clip recorded in this person's voice, played when
    # their card is tapped. Unused in v1.
    announce_clip: Path | None = None


def normalize_uid(uid: str) -> str:
    """Canonical form for an NFC uid (hex, no separators, upper case)."""
    cleaned = uid.strip().upper().replace(":", "").replace("-", "").replace(" ", "")
    if not cleaned:
        raise ValueError("card uid is empty")
    return cleaned


class RecipientDirectory:
    """The current recipient, defaulting to the family chat.

    Card registration and selection are the stable hooks a future PN532
    driver will call. They are not wired to hardware in v1. Passing time
    into :meth:`revert_if_idle` is enough for the idle-timeout behaviour
    once something starts calling :meth:`select_card`.
    """

    def __init__(self, family: Recipient, *, idle_timeout_seconds: float = 120.0) -> None:
        if idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be positive")
        if not family.key:
            raise ValueError("family recipient needs a key")
        self.family = family
        self.idle_timeout_seconds = idle_timeout_seconds
        self._by_key: dict[str, Recipient] = {family.key: family}
        self._cards: dict[str, str] = {}
        self._current = family
        self._selected_at: float | None = None

    @property
    def current(self) -> Recipient:
        return self._current

    def add_recipient(self, recipient: Recipient) -> None:
        """Remember a person a future card can point at."""
        if not recipient.key:
            raise ValueError("recipient key is empty")
        self._by_key[recipient.key] = recipient

    def register_card(self, uid: str, recipient_key: str) -> None:
        """Map a spare card or fob to a recipient.

        Re-registering the same uid replaces the previous person. The
        roadmap describes when the box will enter this mode; v1 only
        exposes the operation.
        """
        if recipient_key not in self._by_key:
            raise KeyError(recipient_key)
        self._cards[normalize_uid(uid)] = recipient_key

    def select_card(self, uid: str, now: float) -> Recipient | None:
        """Switch the current recipient because a card was tapped.

        Returns None when the uid is not registered. The current recipient
        is left unchanged in that case.
        """
        key = self._cards.get(normalize_uid(uid))
        if key is None:
            return None
        self._current = self._by_key[key]
        self._selected_at = now
        return self._current

    def note_activity(self, now: float) -> None:
        """Reset the idle timer after a send or playback, once a card is active."""
        if self._current.key != self.family.key and self._selected_at is not None:
            self._selected_at = now

    def revert_to_default(self) -> Recipient:
        self._current = self.family
        self._selected_at = None
        return self._current

    def revert_if_idle(self, now: float) -> bool:
        """Return to the family chat when the idle timeout has elapsed.

        Returns True when the current recipient actually changed.
        """
        if self._current.key == self.family.key or self._selected_at is None:
            return False
        if now - self._selected_at >= self.idle_timeout_seconds:
            self.revert_to_default()
            return True
        return False
