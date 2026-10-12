"""Empty allowlist means nobody, not everybody."""

from messagebox.policy import should_accept_chat, should_send_to
from messagebox.retry import retry_delay


def test_empty_allowlist_rejects_incoming_and_outgoing():
    assert should_accept_chat(-100, set()) is False
    assert should_send_to(-100, set()) is False


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
