"""Terminal key-repeat becomes a tap or a hold."""

from messagebox.keyhold import KeyHoldClassifier


def test_a_tap_is_a_short_press_once_the_window_passes():
    keys = KeyHoldClassifier(window=0.7, gap=0.22, tap_seconds=0.12)
    assert keys.key_down(0) == []
    assert keys.poll(0.69) == []
    edges = keys.poll(0.7)
    assert [edge.kind for edge in edges] == ["press", "release"]
    assert edges[0].at == 0
    assert edges[1].at == 0.12
    assert keys.pending is False


def test_repeats_hold_until_they_stop():
    keys = KeyHoldClassifier(window=0.7, gap=0.22, tap_seconds=0.12)
    assert keys.key_down(0) == []
    started = keys.key_down(0.4)
    assert [edge.kind for edge in started] == ["press"]
    assert started[0].at == 0
    assert keys.key_down(0.55) == []
    assert keys.poll(0.70) == []  # 0.15s since the last repeat
    released = keys.poll(0.78)
    assert [edge.kind for edge in released] == ["release"]
    assert released[0].at == 0.78
