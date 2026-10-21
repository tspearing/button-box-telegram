"""The family chat is the default. A card can select another chat."""

import pytest

from messagebox.recipients import Recipient, RecipientDirectory


def _book():
    family = Recipient(key="family", chat_id=-100, display_name="Family")
    book = RecipientDirectory(family, idle_timeout_seconds=10)
    book.add_recipient(Recipient(key="person_a", chat_id=-200, display_name="Person A"))
    return book


def test_default_is_the_family_chat():
    book = _book()
    assert book.current.key == "family"
    assert book.current.chat_id == -100


def test_card_selects_a_person_and_idle_time_reverts():
    book = _book()
    book.register_card("04:a1:b2", "person_a")
    chosen = book.select_card("04A1B2", now=100)
    assert chosen is not None
    assert chosen.display_name == "Person A"
    assert book.current.key == "person_a"
    book.note_activity(105)
    assert book.revert_if_idle(114) is False
    assert book.current.key == "person_a"
    assert book.revert_if_idle(115) is True
    assert book.current.key == "family"
    assert book.current.chat_id == -100


def test_unknown_card_does_not_change_the_recipient():
    book = _book()
    book.register_card("aa-bb", "person_a")
    book.select_card("aabb", now=1)
    assert book.select_card("cc-dd", now=2) is None
    assert book.current.key == "person_a"


def test_reregistering_a_spare_card_replaces_the_person():
    book = _book()
    book.add_recipient(Recipient(key="person_b", chat_id=-300, display_name="Person B"))
    book.register_card("0102", "person_a")
    book.register_card("01:02", "person_b")
    chosen = book.select_card("0102", now=0)
    assert chosen is not None and chosen.key == "person_b"


def test_unknown_recipient_cannot_be_registered():
    book = _book()
    with pytest.raises(KeyError):
        book.register_card("0102", "unknown")


def test_explicit_revert():
    book = _book()
    book.register_card("0102", "person_a")
    book.select_card("0102", now=0)
    book.revert_to_default()
    assert book.current.key == "family"
    assert book.revert_if_idle(1000) is False
