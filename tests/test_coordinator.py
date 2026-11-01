"""A short press plays the focused chat: unheard notes, then its latest."""

from messagebox.button_machine import ButtonMachine
from messagebox.cards import build_directory
from messagebox.config import CardConfig
from messagebox.coordinator import (
    Coordinator,
    EffectKind,
    LAMP_ELSEWHERE,
    LAMP_OFF,
    LAMP_RECORDING,
    LAMP_WAITING,
)
from messagebox.queue_store import Inbox
from messagebox.selection import load_selection, save_selection

CHAT = -1000000000001
OTHER = -1000000000002


def _box(tmp_path, *, chat_id=CHAT, play_empty_tone=False, **machine_kwargs):
    defaults = dict(
        hold_seconds=1.0,
        min_record_seconds=1.0,
        max_record_seconds=5.0,
        debounce_seconds=0.05,
    )
    defaults.update(machine_kwargs)
    inbox = Inbox(tmp_path / "inbox")
    focus = {"chat_id": chat_id}
    coord = Coordinator(
        ButtonMachine(**defaults),
        inbox,
        play_empty_tone=play_empty_tone,
        focused_chat=lambda: focus["chat_id"],
    )
    return coord, inbox, focus


def _note(inbox, chat_id=CHAT, payload=b"note"):
    return inbox.enqueue_bytes(payload, meta={"source_chat_id": chat_id})


def _kinds(effects):
    return [effect.kind for effect in effects]


def _tap(coord, at):
    coord.press(at)
    return coord.release(at + 0.2)


def _played(effects):
    return next(effect for effect in effects if effect.kind is EffectKind.PLAY)


def test_boot_lamp_follows_the_focused_chat(tmp_path):
    coord, inbox, _focus = _box(tmp_path)
    assert coord.boot()[0].lamp == LAMP_OFF
    _note(inbox, OTHER, b"other")
    coord._lamp = None
    assert coord.boot()[-1].lamp == LAMP_ELSEWHERE
    _note(inbox, CHAT, b"waiting")
    coord._lamp = None
    assert coord.boot()[-1].lamp == LAMP_WAITING


def test_short_press_on_a_chat_with_no_notes_does_nothing(tmp_path):
    coord, inbox, _focus = _box(tmp_path)
    other = _note(inbox, OTHER, b"other")
    effects = _tap(coord, 0)
    assert EffectKind.PLAY not in _kinds(effects)
    assert EffectKind.EMPTY not in _kinds(effects)
    assert inbox.get(other.id) is not None
    assert inbox.get(other.id).meta.get("heard") is not True
    assert coord.lamp_mode() == LAMP_ELSEWHERE


def test_empty_tone_is_optional_and_does_not_play_another_chat(tmp_path):
    coord, inbox, _focus = _box(tmp_path, play_empty_tone=True)
    other = _note(inbox, OTHER, b"other")
    effects = _tap(coord, 0)
    assert EffectKind.EMPTY in _kinds(effects)
    assert EffectKind.PLAY not in _kinds(effects)
    assert inbox.get(other.id).path.read_bytes() == b"other"


def test_short_press_plays_oldest_in_this_chat_and_keeps_it_after_success(tmp_path):
    coord, inbox, _focus = _box(tmp_path)
    _note(inbox, OTHER, b"older-other")
    first = _note(inbox, CHAT, b"first")
    second = _note(inbox, CHAT, b"second")
    coord.boot()
    play = _played(_tap(coord, 0))
    assert play.item_id == first.id
    assert play.replay is False
    assert len(inbox) == 3  # still unheard until playback finishes
    assert coord.lamp_mode() == LAMP_WAITING

    # Another tap while it is playing does not skip ahead.
    again = _tap(coord, 1)
    assert EffectKind.PLAY not in _kinds(again)
    assert coord.playing_id == first.id

    coord.playback_finished(first.id, success=False)
    assert inbox.get(first.id).meta.get("heard") is not True
    assert coord.playing_id is None

    play = _played(_tap(coord, 2))
    assert play.item_id == first.id
    assert play.replay is False
    coord.playback_finished(first.id, success=True)
    assert inbox.get(first.id).meta["heard"] is True
    assert inbox.get(second.id).meta.get("heard") is not True
    assert coord.lamp_mode() == LAMP_WAITING

    play = _played(_tap(coord, 3))
    assert play.item_id == second.id
    assert play.replay is False


def test_replay_latest_until_a_new_note_arrives_in_this_chat(tmp_path):
    coord, inbox, _focus = _box(tmp_path)
    first = _note(inbox, CHAT, b"first")
    second = _note(inbox, CHAT, b"second")
    _tap(coord, 0)
    coord.playback_finished(first.id, success=True)
    _tap(coord, 1)
    coord.playback_finished(second.id, success=True)
    assert inbox.get(first.id) is None
    assert inbox.get(second.id).path.read_bytes() == b"second"
    assert coord.lamp_mode() == LAMP_OFF

    replay = _played(_tap(coord, 2))
    assert replay.item_id == second.id
    assert replay.replay is True
    coord.playback_finished(second.id, success=True)
    again = _played(_tap(coord, 3))
    assert again.item_id == second.id
    assert again.replay is True
    coord.playback_finished(second.id, success=True)
    assert inbox.get(second.id) is not None

    # A note for a different chat does not end the replay.
    other = _note(inbox, OTHER, b"elsewhere")
    arrived = coord.message_enqueued()
    assert EffectKind.PLAY not in _kinds(arrived)
    assert coord.lamp_mode() == LAMP_ELSEWHERE
    still = _played(_tap(coord, 4))
    assert still.item_id == second.id
    assert still.replay is True
    coord.playback_finished(second.id, success=True)
    assert inbox.get(other.id).meta.get("heard") is not True

    fresh = _note(inbox, CHAT, b"third")
    assert coord.message_enqueued()[-1].lamp == LAMP_WAITING
    play = _played(_tap(coord, 5))
    assert play.item_id == fresh.id
    assert play.replay is False
    coord.playback_finished(fresh.id, success=True)
    assert inbox.get(second.id) is None
    assert inbox.get(fresh.id).path.read_bytes() == b"third"
    assert _played(_tap(coord, 6)).replay is True


def test_switching_focus_plays_that_chat(tmp_path):
    coord, inbox, focus = _box(tmp_path)
    first = _note(inbox, CHAT, b"a1")
    second = _note(inbox, CHAT, b"a2")
    other = _note(inbox, OTHER, b"b1")
    assert _played(_tap(coord, 0)).item_id == first.id
    coord.playback_finished(first.id, success=True)

    focus["chat_id"] = OTHER
    coord.focus_changed()
    assert coord.lamp_mode() == LAMP_WAITING
    assert _played(_tap(coord, 1)).item_id == other.id
    assert inbox.get(second.id).meta.get("heard") is not True
    coord.playback_finished(other.id, success=True)
    assert _played(_tap(coord, 2)).replay is True
    coord.playback_finished(other.id, success=True)

    focus["chat_id"] = CHAT
    assert coord.focus_changed()[-1].lamp == LAMP_WAITING
    assert _played(_tap(coord, 3)).item_id == second.id
    coord.playback_finished(second.id, success=True)
    assert inbox.get(first.id) is None
    assert inbox.get(other.id) is not None
    assert coord.lamp_mode() == LAMP_OFF


def test_replay_and_focus_survive_a_restart(tmp_path):
    root = tmp_path / "data"
    cards = (
        CardConfig(uid="04A1B2C3D4E5F6", chat_id=CHAT, announce="German"),
        CardConfig(uid="04B1C2D3E4F506", chat_id=OTHER, announce="English"),
    )
    inbox = Inbox(root / "inbox")
    waiting = _note(inbox, OTHER, b"waiting")
    heard = _note(inbox, CHAT, b"heard")
    inbox.mark_heard(heard.id)
    save_selection(root / "selection.json", chat_id=CHAT, uid="04A1B2C3D4E5F6")

    reopened = Inbox(root / "inbox")
    book = build_directory(CHAT, cards, 120)
    saved = load_selection(root / "selection.json")
    assert saved is not None
    chosen = book.select_card(saved[0], 0)
    assert chosen is not None and chosen.chat_id == CHAT
    coord = Coordinator(
        ButtonMachine(hold_seconds=1.0, min_record_seconds=1.0, max_record_seconds=5.0),
        reopened,
        focused_chat=lambda: book.current.chat_id,
    )
    assert coord.boot()[-1].lamp == LAMP_ELSEWHERE
    replay = _played(_tap(coord, 0))
    assert replay.item_id == heard.id
    assert replay.replay is True
    assert reopened.get(waiting.id).path.read_bytes() == b"waiting"
    assert reopened.get(waiting.id).meta.get("heard") is not True


def test_single_chat_without_cards_uses_the_default_chat(tmp_path):
    family = CHAT
    book = build_directory(family, (), 120)
    assert book.current.chat_id == family
    inbox = Inbox(tmp_path / "inbox")
    own = _note(inbox, family, b"own")
    _note(inbox, OTHER, b"else")
    coord = Coordinator(
        ButtonMachine(hold_seconds=1.0, min_record_seconds=1.0, max_record_seconds=5.0),
        inbox,
        focused_chat=lambda: book.current.chat_id,
    )
    play = _played(_tap(coord, 0))
    assert play.item_id == own.id
    assert play.replay is False
    coord.playback_finished(own.id, success=True)
    assert _played(_tap(coord, 1)).replay is True


def test_incoming_during_recording_does_not_play(tmp_path):
    coord, inbox, _focus = _box(tmp_path)
    coord.press(0)
    started = coord.tick(1)
    assert EffectKind.START_RECORD in _kinds(started)
    assert coord.lamp_mode() == LAMP_RECORDING
    item = _note(inbox, CHAT, b"from-family")
    _note(inbox, OTHER, b"other-chat")
    arrived = coord.message_enqueued()
    assert EffectKind.PLAY not in _kinds(arrived)
    assert coord.playing_id is None
    assert coord.recording
    stopped = coord.release(3)
    assert EffectKind.STOP_RECORD in _kinds(stopped)
    assert EffectKind.PLAY not in _kinds(stopped)
    assert coord.playing_id is None
    played = _played(_tap(coord, 4))
    assert played.item_id == item.id


def test_hold_uses_the_recording_lamp_until_they_let_go_at_max(tmp_path):
    coord, inbox, _focus = _box(tmp_path)
    _note(inbox, OTHER, b"elsewhere")
    coord.press(0)
    coord.tick(1)
    assert coord.lamp_mode() == LAMP_RECORDING
    capped = coord.tick(6)
    assert EffectKind.STOP_RECORD in _kinds(capped)
    assert coord.recording
    assert coord.lamp_mode() == LAMP_RECORDING
    coord.release(7)
    assert not coord.recording
    assert coord.lamp_mode() == LAMP_ELSEWHERE


def test_threshold_release_does_not_pretend_the_mic_opened(tmp_path):
    coord, _inbox, _focus = _box(tmp_path)
    coord.press(0)
    effects = coord.release(1.0)  # exactly the hold time, no tick
    assert EffectKind.START_RECORD not in _kinds(effects)
    assert EffectKind.DISCARD_RECORD in _kinds(effects)
    assert not coord.recording
