"""Allowlist rules.

An empty allowlist rejects every chat. It is never treated as "allow all".
``/chatid`` is the one command that still answers, so a new box can learn
its family chat id before the allowlist is filled in. That command is
handled separately and only replies with the asking chat's own id.
"""

from __future__ import annotations


def should_accept_chat(chat_id: int, allowed: set[int]) -> bool:
    """True when a voice note or audio file from this chat may be queued."""
    if not allowed:
        return False
    return chat_id in allowed


def should_send_to(chat_id: int, allowed: set[int]) -> bool:
    """True when an outgoing voice note may be delivered to this chat."""
    if not chat_id or not allowed:
        return False
    return chat_id in allowed
