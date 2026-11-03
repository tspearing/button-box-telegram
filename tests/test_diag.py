"""The announcement diagnostic does not need the speaker."""

from pathlib import Path

from messagebox.announce import Announcer, SpeechAttempt
from messagebox.diag import main


def test_diag_writes_the_clip_and_does_not_play(tmp_path, monkeypatch):
    def synth(text: str, dest: Path) -> bool:
        dest.write_bytes(b"RIFF" + b"\x00" * 80)
        return True

    def build(*_args, **_kwargs):
        return Announcer(tmp_path / "cache", attempts=[SpeechAttempt("custom", synth)])

    def fail_play(*_args, **_kwargs):
        raise AssertionError("speaker was opened")

    monkeypatch.setattr("messagebox.diag.build_announcer", build)
    monkeypatch.setattr("messagebox.diag.AudioEngine.play_cmd", fail_play)
    config = tmp_path / "config.toml"
    config.write_text("[audio]\nlead_in_ms = 850\n", encoding="utf-8")
    written = tmp_path / "out" / "german.wav"
    code = main(
        [
            "--config",
            str(config),
            "announce",
            "German",
            "--lead-in-mode",
            "noise",
            "--lead-in-ms",
            "850",
            "--no-play",
            "--write",
            str(written),
        ]
    )
    assert code == 0
    assert written.read_bytes().startswith(b"RIFF")
