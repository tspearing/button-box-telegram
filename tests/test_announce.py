"""Card names: Piper first, espeak-ng if that fails, a wav file before either."""

from pathlib import Path

from messagebox.announce import (
    Announcer,
    PiperSpeaker,
    SpeechAttempt,
    build_announcer,
)


def _wav(path: Path, payload: bytes = b"RIFF" + b"\x00" * 80) -> None:
    path.write_bytes(payload)


def test_wav_override_wins_over_both_engines(tmp_path):
    def boom(text: str, dest: Path) -> bool:
        raise AssertionError(text)

    announcer = Announcer(
        tmp_path / "cache",
        attempts=[
            SpeechAttempt("piper|voice|0|1.000", boom),
            SpeechAttempt("espeak-ng", boom),
        ],
    )
    override = tmp_path / "name.wav"
    _wav(override)
    assert announcer.clip_for("German", override) == override


def test_piper_success_does_not_call_espeak(tmp_path):
    calls: list[str] = []

    def piper(text: str, dest: Path) -> bool:
        calls.append("piper:" + text)
        _wav(dest)
        return True

    def espeak(text: str, dest: Path) -> bool:
        calls.append("espeak:" + text)
        _wav(dest)
        return True

    announcer = Announcer(
        tmp_path / "cache",
        attempts=[
            SpeechAttempt("piper|voice|1|1.000", piper),
            SpeechAttempt("espeak-ng", espeak),
        ],
    )
    first = announcer.clip_for("German")
    second = announcer.clip_for("German")
    assert first == second
    assert calls == ["piper:German"]


def test_piper_failure_falls_back_to_espeak(tmp_path):
    def piper(text: str, dest: Path) -> bool:
        return False

    def espeak(text: str, dest: Path) -> bool:
        _wav(dest, b"RIFF" + b"e" * 80)
        return True

    announcer = Announcer(
        tmp_path / "cache",
        attempts=[
            SpeechAttempt("piper|voice|0|1.000", piper),
            SpeechAttempt("espeak-ng", espeak),
        ],
    )
    clip = announcer.clip_for("English")
    assert clip is not None
    assert clip.read_bytes().startswith(b"RIFF")
    assert b"e" * 80 in clip.read_bytes()
    # The Piper attempt did not leave a clip that would block a later try.
    assert list((tmp_path / "cache").glob("*.wav")) == [clip]


def test_cache_key_includes_engine_and_voice(tmp_path):
    calls: list[str] = []

    def synth(text: str, dest: Path) -> bool:
        calls.append(text)
        _wav(dest)
        return True

    cache = tmp_path / "cache"
    piper = Announcer(cache, attempts=[SpeechAttempt("piper|/voices/a.onnx|10|1.000", synth)])
    espeak = Announcer(cache, attempts=[SpeechAttempt("espeak-ng", synth)])
    other = Announcer(cache, attempts=[SpeechAttempt("piper|/voices/b.onnx|10|1.000", synth)])
    first = piper.clip_for("German")
    second = espeak.clip_for("German")
    third = other.clip_for("German")
    assert first != second
    assert first != third
    assert second != third
    assert calls == ["German", "German", "German"]
    assert piper.clip_for("German") == first
    assert calls == ["German", "German", "German"]


def test_build_announcer_defaults_to_piper_then_espeak(tmp_path):
    voice = Path("/opt/message-box/voices/en_US-lessac-medium.onnx")
    announcer = build_announcer(
        tmp_path / "cache",
        engine="piper",
        piper_voice=voice,
        piper_length_scale=1,
        sample_rate=48000,
    )
    assert announcer.attempts[0].cache_key.startswith(f"piper|{voice}|")
    assert announcer.attempts[0].cache_key.endswith("|1.000")
    assert announcer.attempts[1].cache_key == "espeak-ng"
    slower = build_announcer(
        tmp_path / "cache",
        engine="piper",
        piper_voice=voice,
        piper_length_scale=1.2,
        sample_rate=48000,
    )
    assert slower.attempts[0].cache_key != announcer.attempts[0].cache_key
    espeak = build_announcer(
        tmp_path / "cache",
        engine="espeak",
        piper_voice=voice,
        piper_length_scale=1,
        sample_rate=48000,
    )
    assert [item.cache_key for item in espeak.attempts] == ["espeak-ng"]


def test_missing_piper_voice_does_not_raise(tmp_path):
    model = tmp_path / "missing.onnx"
    speaker = PiperSpeaker(model, 1.0, synthesize=lambda *args: None)
    dest = tmp_path / "out.wav"
    assert speaker("German", dest) is False
    assert not dest.exists()


def test_piper_speaker_writes_the_resampled_wav(tmp_path):
    model = tmp_path / "en_US-lessac-medium.onnx"
    model.write_bytes(b"model")
    Path(str(model) + ".json").write_text("{}", encoding="utf-8")
    seen: dict[str, float] = {}

    def synthesize(path: Path, text: str, dest: Path, length_scale: float) -> None:
        seen["scale"] = length_scale
        seen["text"] = text
        dest.write_bytes(b"RIFF" + b"p" * 80)

    def resample(source: Path, dest: Path, *, rate: int) -> bool:
        assert rate == 48000
        assert source.read_bytes().startswith(b"RIFF")
        dest.write_bytes(b"RIFF" + b"r" * 80)
        return True

    speaker = PiperSpeaker(
        model,
        1.1,
        sample_rate=48000,
        synthesize=synthesize,
        resample=resample,
    )
    dest = tmp_path / "clip.wav"
    assert speaker("English", dest) is True
    assert seen == {"scale": 1.1, "text": "English"}
    assert dest.read_bytes() == b"RIFF" + b"r" * 80
    assert not list(tmp_path.glob("*.piper.wav"))
