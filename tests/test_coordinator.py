"""Short press plays the oldest note. Incoming audio never starts playback."""

from messagebox.button_machine import ButtonMachine
from messagebox.coordinator import Coordinator, EffectKind, LAMP_OFF, LAMP_RECORDING, LAMP_WAITING
from messagebox.queue_store import DiskQueue


def _box(tmp_path, **machine_kwargs):
    defaults = dict(
        hold_seconds=1.0,
        min_record_seconds=1.0,
        max_record_seconds=5.0,
        debounce_seconds=0.05,
    )
    defaults.update(machine_kwargs)
    inbox = DiskQueue(tmp_path / "inbox")
    coord = Coordinator(ButtonMachine(**defaults), inbox, play_empty_tone=False)
    return coord, inbox


def _kinds(effects):
    return [effect.kind for effect in effects]


def test_boot_lamp_follows_a_queue_that_survived_reboot(tmp_path):
    coord, inbox = _box(tmp_path)
    assert coord.boot()[0].lamp == LAMP_OFF
    inbox.enqueue_bytes(b"waiting")
    coord._lamp = None
    effects = coord.boot()
    assert effects[-1].lamp == LAMP_WAITING


def test_short_press_on_an_empty_queue_does_nothing(tmp_path):
    coord, _inbox = _box(tmp_path)
    coord.press(0)
    effects = coord.release(0.2)
    assert EffectKind.PLAY not in _kinds(effects)
    assert EffectKind.EMPTY not in _kinds(effects)
    assert coord.lamp_mode() == LAMP_OFF


def test_empty_tone_is_optional(tmp_path):
    _coord, inbox = _box(tmp_path)
    coord = Coordinator(_coord.machine, inbox, play_empty_tone=True)
    coord.press(0)
    effects = coord.release(0.2)
    assert EffectKind.EMPTY in _kinds(effects)


def test_short_press_plays_oldest_and_removes_it_only_after_success(tmp_path):
    coord, inbox = _box(tmp_path)
    first = inbox.enqueue_bytes(b"first")
    second = inbox.enqueue_bytes(b"second")
    coord.boot()
    coord.press(0)
    effects = coord.release(0.2)
    play = next(effect for effect in effects if effect.kind is EffectKind.PLAY)
    assert play.item_id == first.id
    assert len(inbox) == 2  # still queued until playback finishes
    assert coord.lamp_mode() == LAMP_WAITING

    # Another tap while it is playing does not skip ahead.
    coord.press(1)
    again = coord.release(1.2)
    assert EffectKind.PLAY not in _kinds(again)
    assert coord.playing_id == first.id

    coord.playback_finished(first.id, success=False)
    assert inbox.get(first.id) is not None
    assert coord.playing_id is None

    coord.press(2)
    effects = coord.release(2.2)
    assert next(effect.item_id for effect in effects if effect.kind is EffectKind.PLAY) == first.id
    coord.playback_finished(first.id, success=True)
    assert inbox.get(first.id) is None
    assert inbox.peek().id == second.id
    assert coord.lamp_mode() == LAMP_WAITING


def test_incoming_during_recording_does_not_play(tmp_path):
    coord, inbox = _box(tmp_path)
    coord.press(0)
    started = coord.tick(1)
    assert EffectKind.START_RECORD in _kinds(started)
    assert coord.lamp_mode() == LAMP_RECORDING
    item = inbox.enqueue_bytes(b"from-family")
    arrived = coord.message_enqueued()
    assert EffectKind.PLAY not in _kinds(arrived)
    assert coord.playing_id is None
    assert coord.recording
    # Letting go stores the recording; it still does not play the inbox.
    stopped = coord.release(3)
    assert EffectKind.STOP_RECORD in _kinds(stopped)
    assert EffectKind.PLAY not in _kinds(stopped)
    assert coord.playing_id is None
    assert inbox.peek().id == item.id
    coord.press(4)
    played = coord.release(4.2)
    assert next(effect.item_id for effect in played if effect.kind is EffectKind.PLAY) == item.id


def test_hold_uses_the_recording_lamp_until_they_let_go_at_max(tmp_path):
    coord, _inbox = _box(tmp_path)
    coord.press(0)
    coord.tick(1)
    assert coord.lamp_mode() == LAMP_RECORDING
    capped = coord.tick(6)
    assert EffectKind.STOP_RECORD in _kinds(capped)
    assert coord.recording
    assert coord.lamp_mode() == LAMP_RECORDING
    coord.release(7)
    assert not coord.recording
    assert coord.lamp_mode() == LAMP_OFF


def test_threshold_release_does_not_pretend_the_mic_opened(tmp_path):
    coord, _inbox = _box(tmp_path)
    coord.press(0)
    effects = coord.release(1.0)  # exactly the hold time, no tick
    assert EffectKind.START_RECORD not in _kinds(effects)
    assert EffectKind.DISCARD_RECORD in _kinds(effects)
    assert not coord.recording
