"""USB mic and speaker detection, with the Pi's own cards ignored."""

from messagebox.alsa import card_from_alsa_device, choose_device, parse_alsa_list
from messagebox.audio import AudioEngine
from messagebox.config import AudioConfig

CAPTURE = """
**** List of CAPTURE Hardware Devices ****
card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
card 1: G11 [TONOR G11], device 0: USB Audio [USB Audio]
card 2: Device [USB Audio Device], device 0: USB Audio [USB Audio]
"""

PLAYBACK = """
**** List of PLAYBACK Hardware Devices ****
card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
card 2: Device [USB Audio Device], device 0: USB Audio [USB Audio]
card 3: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
"""


def test_parse_and_choose_usb_devices():
    mics = parse_alsa_list(CAPTURE)
    assert [device.card for device in mics] == [0, 1, 2]
    mic = choose_device(mics, ["TONOR", "G11", "USB"])
    assert mic is not None
    assert mic.plughw == "plughw:1,0"
    assert "TONOR" in mic.name

    speakers = parse_alsa_list(PLAYBACK)
    speaker = choose_device(speakers, ["LIELONGREN", "USB Audio", "USB"], exclude_cards={mic.card})
    assert speaker is not None
    assert speaker.plughw == "plughw:2,0"


def test_builtin_only_returns_none():
    devices = parse_alsa_list(PLAYBACK)
    builtin = [device for device in devices if device.card != 2]
    assert choose_device(builtin, ["USB"]) is None


def test_card_number_from_a_device_string():
    assert card_from_alsa_device("plughw:2,0") == 2
    assert card_from_alsa_device("hw:1,0") == 1
    assert card_from_alsa_device("default") is None


def test_missing_usb_devices_are_called_out():
    builtin = """
**** List of CAPTURE Hardware Devices ****
card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
"""
    engine = AudioEngine(
        AudioConfig(),
        capture_lister=lambda: builtin,
        playback_lister=lambda: builtin,
    )
    notes = engine.fallback_notes()
    assert any(note.startswith("no USB microphone detected") for note in notes)
    assert "no USB speaker detected, playback went to ALSA default and may be silent" in notes


def test_explicit_devices_are_not_reported_as_missing():
    engine = AudioEngine(
        AudioConfig(mic_device="plughw:1,0", speaker_device="plughw:2,0"),
        capture_lister=lambda: "",
        playback_lister=lambda: "",
    )
    assert engine.fallback_notes() == []


def test_engine_uses_listers_and_overrides():
    engine = AudioEngine(
        AudioConfig(),
        capture_lister=lambda: CAPTURE,
        playback_lister=lambda: PLAYBACK,
    )
    assert engine.resolve_mic() == "plughw:1,0"
    assert engine.resolve_speaker() == "plughw:2,0"

    forced = AudioEngine(
        AudioConfig(mic_device="plughw:9,0", speaker_device="plughw:8,0"),
        capture_lister=lambda: CAPTURE,
        playback_lister=lambda: PLAYBACK,
    )
    assert forced.resolve_mic() == "plughw:9,0"
    assert forced.resolve_speaker() == "plughw:8,0"
