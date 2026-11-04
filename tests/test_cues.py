"""Spoken lines before a received note and after a send."""

from messagebox.cues import SENDER_FALLBACK, sender_intro, startup_cues

MOM = 123456789
DAD = 234567890


def test_sender_intro_uses_first_name_then_username_then_message():
    overrides = {}
    assert (
        sender_intro(
            {"sender_id": MOM, "sender_name": "Mom"},
            enabled=True,
            overrides=overrides,
        )
        == "Message from Mom"
    )
    assert (
        sender_intro(
            {"sender_name": "dad_example"},
            enabled=True,
            overrides=overrides,
        )
        == "Message from dad example"
    )
    assert sender_intro({}, enabled=True, overrides=overrides) == SENDER_FALLBACK
    assert sender_intro({"sender_name": "Mom"}, enabled=False, overrides=overrides) is None


def test_sender_intro_strips_emoji_and_prefers_the_config_name():
    phrase = sender_intro(
        {"sender_id": MOM, "sender_name": "Someone 🎉"},
        enabled=True,
        overrides={MOM: "Mom"},
    )
    assert phrase == "Message from Mom"
    plain = sender_intro(
        {"sender_id": DAD, "sender_name": "Dad 🎉"},
        enabled=True,
        overrides={},
    )
    assert plain == "Message from Dad"


def test_an_older_note_uses_the_stored_label_and_symbols_are_spoken_safely():
    assert (
        sender_intro(
            {"sender_label": "Grandma"},
            enabled=True,
            overrides={},
        )
        == "Message from Grandma"
    )
    assert (
        sender_intro(
            {"sender_name": "Mom & Dad"},
            enabled=True,
            overrides={},
        )
        == "Message from Mom and Dad"
    )


def test_the_same_sender_phrase_is_stable_for_the_speech_cache():
    meta = {"sender_id": MOM, "sender_name": "Mom"}
    first = sender_intro(meta, enabled=True, overrides={})
    second = sender_intro(meta, enabled=True, overrides={})
    assert first == second == "Message from Mom"


def test_startup_cues_include_sent_and_the_generic_message():
    assert startup_cues(
        confirm_sent=True,
        sent_text="Sent",
        not_sent_text="Not sent",
        announce_sender=True,
    ) == ["Sent", "Not sent", "Message"]
    assert startup_cues(
        confirm_sent=False,
        sent_text="Sent",
        not_sent_text="Not sent",
        announce_sender=False,
    ) == []
