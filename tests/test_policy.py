"""Empty allowlist means nobody, not everybody."""

from messagebox.policy import (
    authenticated_sender_id,
    should_accept_chat,
    should_accept_sender,
    should_send_to,
)
from messagebox.retry import retry_delay


def test_empty_allowlist_rejects_incoming_and_outgoing():
    assert should_accept_chat(-100, set()) is False
    assert should_send_to(-100, set()) is False


def test_a_sender_list_does_not_accept_an_unlisted_chat():
    assert should_accept_chat(-1000000000002, {-1000000000001}) is False


def test_missing_and_non_numeric_chats_are_rejected():
    assert should_accept_chat(True, {-1000000000001}) is False  # type: ignore[arg-type]
    assert should_accept_chat(0, {0}) is False


def test_sender_allowlist_fails_closed():
    allowed = {123456789}
    assert should_accept_sender(None, allowed, allow_any_sender=False) is False
    assert should_accept_sender(234567890, set(), allow_any_sender=False) is False
    assert should_accept_sender(123456789, allowed, allow_any_sender=False) is True
    assert should_accept_sender(234567890, allowed, allow_any_sender=False) is False
    assert should_accept_sender(0, allowed, allow_any_sender=True) is False
    assert should_accept_sender(234567890, set(), allow_any_sender=True) is True


class _User:
    def __init__(self, user_id, *, is_bot=False):
        self.id = user_id
        self.is_bot = is_bot


class _Message:
    def __init__(self, user=None, **extra):
        self.from_user = user
        for key, value in extra.items():
            setattr(self, key, value)


def test_only_the_authenticated_from_user_counts():
    approved = _User(123456789)
    stranger = _User(234567890)
    forwarded = _Message(stranger, forward_from=approved, forward_sender_name="Mom")
    assert authenticated_sender_id(forwarded) == 234567890
    assert authenticated_sender_id(_Message(None, sender_chat=_User(-1000000000001))) is None
    assert authenticated_sender_id(_Message(_User(999999999, is_bot=True))) is None
    assert authenticated_sender_id(_Message(_User(123456789))) == 123456789
    assert authenticated_sender_id(None) is None


def test_only_listed_chats_pass():
    allowed = {-100, 42}
    assert should_accept_chat(-100, allowed) is True
    assert should_accept_chat(7, allowed) is False
    assert should_send_to(0, allowed) is False
    assert should_send_to(42, allowed) is True


def test_retry_backoff_caps_at_five_minutes():
    assert retry_delay(1) == 2
    assert retry_delay(2) == 4
    assert retry_delay(8) == 256
    assert retry_delay(9) == 300
    assert retry_delay(20) == 300
    assert retry_delay(0) == 2
