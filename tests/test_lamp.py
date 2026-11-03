"""Lamp patterns: steady while recording, fast and slow blinks otherwise."""

from messagebox.lamp import LampPatterns, lamp_output


def test_default_patterns():
    patterns = LampPatterns()
    assert lamp_output("off", patterns) == ("off", 0.0, 0.0)
    assert lamp_output("recording", patterns) == ("on", 0.0, 0.0)
    assert lamp_output("waiting", patterns) == ("blink", 0.15, 0.15)
    assert lamp_output("elsewhere", patterns) == ("blink", 0.5, 2.5)


def test_blink_timings_come_from_the_config():
    patterns = LampPatterns(
        unheard_on_seconds=0.12,
        unheard_off_seconds=0.18,
        elsewhere_on_seconds=0.4,
        elsewhere_off_seconds=2.0,
    )
    assert lamp_output("waiting", patterns) == ("blink", 0.12, 0.18)
    assert lamp_output("elsewhere", patterns) == ("blink", 0.4, 2.0)
    assert lamp_output("recording", patterns) == ("on", 0.0, 0.0)
