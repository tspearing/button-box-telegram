"""Turn ``[[cards]]`` config into the recipient directory the sender already uses."""

from __future__ import annotations

from messagebox.config import CardConfig
from messagebox.recipients import Recipient, RecipientDirectory


def build_directory(
    family_chat_id: int,
    cards: tuple[CardConfig, ...],
    idle_timeout_seconds: float,
) -> RecipientDirectory:
    """One recipient per card, keyed by UID, so two cards can share a chat.

    ``idle_timeout_seconds`` is accepted so older configs still construct a
    directory. The service does not revert when it elapses. A selected card
    stays selected until a different card is tapped.
    """
    family = Recipient(key="family", chat_id=family_chat_id or 0, display_name="Family")
    timeout = idle_timeout_seconds if idle_timeout_seconds > 0 else 120.0
    book = RecipientDirectory(family, idle_timeout_seconds=timeout)
    for card in cards:
        key = f"card-{card.uid}"
        book.add_recipient(
            Recipient(
                key=key,
                chat_id=card.chat_id,
                display_name=card.announce or "Card",
                announce_clip=card.announce_file,
            )
        )
        book.register_card(card.uid, key)
    return book
