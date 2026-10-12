"""Hold versus short press, including discard and the two-minute cap."""

from messagebox.button_machine import ActionKind, ButtonMachine, Phase


def machine(**kwargs) -> ButtonMachine:
    defaults = dict(
        hold_seconds=1.0,
        min_record_seconds=1.0,
        max_record_seconds=5.0,
        debounce_seconds=0.05,
    )
    defaults.update(kwargs)
    return ButtonMachine(**defaults)


def kinds(actions) -> list[ActionKind]:
    return [action.kind for action in actions]


def test_defaults_match_the_product():
    button = ButtonMachine()
    assert button.hold_seconds == 0.6
    assert button.min_record_seconds == 1.0
    assert button.max_record_seconds == 120.0


def test_short_press():
    button = machine()
    assert button.press(0) == []
    actions = button.release(0.4)
    assert kinds(actions) == [ActionKind.SHORT_PRESS]
    assert button.phase is Phase.IDLE


def test_debounce_is_not_a_short_press():
    button = machine()
    button.press(0)
    assert button.release(0.04) == []
    assert button.phase is Phase.IDLE


def test_second_short_press_still_works():
    button = machine()
    button.press(0)
    button.release(0.2)
    button.press(1)
    assert kinds(button.release(1.3)) == [ActionKind.SHORT_PRESS]


def test_hold_starts_at_the_threshold():
    button = machine()
    button.press(0)
    assert button.tick(0.999) == []
    assert button.phase is Phase.DOWN
    actions = button.tick(1.0)
    assert kinds(actions) == [ActionKind.RECORD_START]
    assert button.phase is Phase.RECORDING


def test_release_just_after_the_beep_discards():
    button = machine()
    button.press(0)
    button.tick(1.0)
    actions = button.release(1.5)  # 0.5s of audio
    assert kinds(actions) == [ActionKind.RECORD_DISCARD]
    assert actions[0].recorded_for == 0.5
    assert button.phase is Phase.IDLE


def test_release_after_enough_audio_stops():
    button = machine()
    button.press(10)
    button.tick(11)
    actions = button.release(12)  # exactly the 1s minimum
    assert kinds(actions) == [ActionKind.RECORD_STOP]
    assert actions[0].recorded_for == 1.0


def test_release_exactly_at_the_hold_threshold_without_a_tick():
    button = machine()
    button.press(0)
    actions = button.release(1.0)
    assert kinds(actions) == [ActionKind.RECORD_START, ActionKind.RECORD_DISCARD]
    assert button.phase is Phase.IDLE


def test_max_length_stops_and_ignores_the_rest_of_the_hold():
    button = machine()
    button.press(0)
    assert kinds(button.tick(1)) == [ActionKind.RECORD_START]
    actions = button.tick(6)  # record_at is 1, so 5s of audio
    assert kinds(actions) == [ActionKind.RECORD_MAX]
    assert actions[0].recorded_for == 5.0
    assert button.phase is Phase.HELD_AFTER_MAX
    assert button.tick(7) == []
    assert button.press(7) == []
    assert kinds(button.release(8)) == [ActionKind.HOLD_CLEARED]
    assert button.phase is Phase.IDLE
    # A new tap works after they let go.
    button.press(9)
    assert kinds(button.release(9.2)) == [ActionKind.SHORT_PRESS]


def test_extra_press_and_release_while_idle_are_ignored():
    button = machine()
    button.press(0)
    assert button.press(0.1) == []
    assert button.phase is Phase.DOWN
    button = machine()
    assert button.release(0) == []


def test_real_default_timings_cross_the_boundary():
    button = ButtonMachine()
    button.press(0)
    assert button.tick(0.6)[0].kind is ActionKind.RECORD_START
    # 0.6s qualification + 1.0s of audio.
    button = ButtonMachine()
    button.press(0)
    button.tick(0.6)
    assert button.release(1.6)[0].kind is ActionKind.RECORD_STOP
    button = ButtonMachine()
    button.press(0)
    button.tick(0.6)
    assert button.release(1.2)[0].kind is ActionKind.RECORD_DISCARD
