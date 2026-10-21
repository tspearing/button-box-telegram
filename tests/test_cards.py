"""Card selection, saved choice, and announcements. Speech is injected."""

from pathlib import Path

from messagebox.announce import Announcer, espeak_ng_command, espeak_ng_synth
from messagebox.cards import build_directory
from messagebox.config import CardConfig
from messagebox.selection import load_selection, save_selection


def _cards() -> tuple[CardConfig, ...]:
    return (
        CardConfig(uid="04A1B2C3D4E5F6", chat_id=-1000000000001, announce="German"),
        CardConfig(
            uid="04B1C2D3E4F506",
            chat_id=-1000000000002,
            announce="English",
            announce_file=Path("/var/lib/message-box/clips/english.wav"),
        ),
    )


def test_selection_sticks_and_is_restored():
    book = build_directory(-1000000000001, _cards(), idle_timeout_seconds=120)
    assert book.current.chat_id == -1000000000001
    chosen = book.select_card("04b1c2d3e4f506", now=10)
    assert chosen is not None
    assert chosen.chat_id == -1000000000002
    assert chosen.display_name == "English"
    assert book.select_card("04A1B2C3D4E5F6", now=50) is not None
    assert book.current.display_name == "German"
    assert book.select_card("00112233445566", now=60) is None
    assert book.current.display_name == "German"


def test_saved_uid_restores_without_depending_on_the_idle_timer(tmp_path):
    path = tmp_path / "selection.json"
    save_selection(path, chat_id=-1000000000002, uid="04:b1:c2:d3:e4:f5:06")
    assert load_selection(path) == ("04B1C2D3E4F506", -1000000000002)
    book = build_directory(-1000000000001, _cards(), idle_timeout_seconds=10)
    saved = load_selection(path)
    assert saved is not None
    chosen = book.select_card(saved[0], now=1)
    assert chosen is not None and chosen.chat_id == -1000000000002
    path.write_text("{", encoding="utf-8")
    assert load_selection(path) is None
    assert load_selection(tmp_path / "missing.json") is None


def test_removed_card_does_not_restore():
    book = build_directory(-1000000000001, _cards()[:1], idle_timeout_seconds=120)
    assert book.select_card("04B1C2D3E4F506", now=1) is None
    assert book.current.key == "family"


def test_announcer_caches_and_prefers_a_real_file(tmp_path):
    calls: list[str] = []

    def synth(text: str, dest: Path) -> bool:
        calls.append(text)
        dest.write_bytes(b"RIFF" + b"\x00" * 80)
        return True

    announcer = Announcer(tmp_path / "cache", synth)
    first = announcer.clip_for("German", None)
    second = announcer.clip_for("German", None)
    assert first == second
    assert calls == ["German"]
    override = tmp_path / "clip.wav"
    override.write_bytes(b"RIFF" + b"\x00" * 80)
    assert announcer.clip_for("German", override) == override
    assert calls == ["German"]
    missing = tmp_path / "nope.wav"
    assert announcer.clip_for("English", missing) is not None
    assert calls == ["German", "English"]


def test_announcer_returns_none_when_synthesis_fails(tmp_path):
    def synth(text: str, dest: Path) -> bool:
        dest.write_bytes(b"nope")
        return False

    assert Announcer(tmp_path, synth).clip_for("German", None) is None

    def boom(text: str, dest: Path) -> bool:
        raise RuntimeError("synth")

    assert Announcer(tmp_path, boom).clip_for("English", None) is None


def test_espeak_command_does_not_treat_the_phrase_as_a_flag(tmp_path, monkeypatch):
    command = espeak_ng_command("--help", tmp_path / "out.wav")
    assert command[-2] == "--"
    assert command[-1] == "--help"
    monkeypatch.setattr("messagebox.announce.shutil.which", lambda _name: None)
    assert espeak_ng_synth("German", tmp_path / "a.wav") is False
