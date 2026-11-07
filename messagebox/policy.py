"""Allowlist rules.

An empty allowlist rejects every chat. It is never treated as "allow all".
``/chatid`` is the one command that still answers, so a new box can learn
its family chat id before the allowlist is filled in. That command is
handled separately and only replies with the asking chat's own id.
"""

from __future__ import annotations


def should_accept_chat(chat_id: int, allowed: set[int]) -> bool:
    """True when a voice note or audio file from this chat may be queued.

    An empty allowlist rejects every chat. A sender allowlist does not add
    chats: the chat id has to be listed on its own.
    """
    if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id == 0:
        return False
    if not allowed:
        return False
    return chat_id in allowed


def authenticated_sender_id(message) -> int | None:
    """User id Telegram authenticated as the author of this message.

    This is ``from_user`` only. Forward metadata, a display name, and a
    channel or anonymous ``sender_chat`` are not an identity. Bots and a
    missing user are rejected.
    """
    if message is None:
        return None
    user = getattr(message, "from_user", None)
    if user is None:
        return None
    # A missing flag fails closed. Anonymous group posts are a bot identity.
    if getattr(user, "is_bot", True):
        return None
    user_id = getattr(user, "id", None)
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        return None
    return user_id


def should_accept_sender(
    sender_id: int | None,
    allowed_senders: set[int],
    *,
    allow_any_sender: bool,
) -> bool:
    """True when this authenticated user may be heard.

    ``allow_any_sender`` is an explicit opt-in. It still requires a real
    positive user id, and it does not accept a chat that is not listed.
    An empty sender list rejects everyone when that opt-in is off.
    """
    if isinstance(sender_id, bool) or not isinstance(sender_id, int) or sender_id <= 0:
        return False
    if allow_any_sender:
        return True
    if not allowed_senders:
        return False
    return sender_id in allowed_senders


def should_send_to(chat_id: int, allowed: set[int]) -> bool:
    """True when an outgoing voice note may be delivered to this chat."""
    if not chat_id or not allowed:
        return False
    return chat_id in allowed
