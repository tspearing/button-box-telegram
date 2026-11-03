"""Config, token loading, and the family-chat allowlist rule."""

from pathlib import Path

import pytest

from messagebox.config import (
    SYSTEM_CONFIG_PATH,
    AppConfig,
    TelegramConfig,
    load_config,
    load_token,
    normalize_token,
    parse_env_text,
    resolve_config_path,
    resolved_family_chat_id,
)

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_loads():
    config = load_config(ROOT / "config.example.toml", dev=False)
    assert config.button.pin == 17
    assert config.lamp.pin == 26
    assert config.lamp.enabled is True
    assert config.lamp.unheard_on_seconds == 0.5
    assert config.lamp.unheard_off_seconds == 2.5
    assert config.lamp.elsewhere_on_seconds == 1
    assert config.lamp.elsewhere_off_seconds == 1
    assert config.button.hold_seconds == 0.6
    assert config.button.min_record_seconds == 1.0
    assert config.button.max_record_seconds == 180
    assert config.telegram.allowed_chat_ids == ()
    assert config.telegram.family_chat_id is None
    assert config.audio.play_empty_tone is False
    assert config.audio.lead_in_ms == 300
    assert config.announce.engine == "piper"
    assert config.announce.piper_voice == Path("/opt/message-box/voices/en_US-lessac-medium.onnx")
    assert config.announce.piper_length_scale == 1.0
    assert config.paths.data_dir == Path("/var/lib/message-box")
    assert config.paths.env_file == Path("/etc/message-box/env")
    assert config.cards == ()
    assert config.nfc.i2c_bus == 1
    assert config.nfc.i2c_address == 36
    assert config.nfc.enabled is True


def test_existing_max_record_seconds_is_kept(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[button]\nmax_record_seconds = 120\n", encoding="utf-8")
    config = load_config(path)
    assert config.button.max_record_seconds == 120


def test_omitted_max_record_seconds_defaults_to_three_minutes(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[button]\nhold_seconds = 0.6\n", encoding="utf-8")
    config = load_config(path)
    assert config.button.max_record_seconds == 180


def test_lead_in_defaults_when_the_key_is_omitted(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[audio]\nmic_device = \"auto\"\n", encoding="utf-8")
    assert load_config(path).audio.lead_in_ms == 300


def test_lead_in_rejects_a_negative_pause(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[audio]\nlead_in_ms = -20\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lead_in_ms"):
        load_config(path)


def test_announce_defaults_to_piper_when_the_section_is_missing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[lamp]\nenabled = true\n", encoding="utf-8")
    config = load_config(path)
    assert config.announce.engine == "piper"
    assert config.announce.piper_voice == Path("/opt/message-box/voices/en_US-lessac-medium.onnx")
    assert config.announce.piper_length_scale == 1.0


def test_announce_engine_must_be_known(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[announce]\nengine = "robot"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="announce.engine"):
        load_config(path)


def test_old_lamp_waiting_option_still_loads(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            [
                "[lamp]",
                'waiting = "blink"',
                "waiting_on_seconds = 0.2",
                "waiting_off_seconds = 1.5",
                "recording_on_seconds = 0.05",
                "recording_off_seconds = 0.05",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.lamp.enabled is True
    assert config.lamp.unheard_on_seconds == 0.5
    assert config.lamp.unheard_off_seconds == 2.5
    assert config.lamp.elsewhere_on_seconds == 1
    assert config.lamp.elsewhere_off_seconds == 1


def test_lamp_blink_timings_are_configurable(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            [
                "[lamp]",
                "unheard_on_seconds = 0.12",
                "unheard_off_seconds = 0.18",
                "elsewhere_on_seconds = 0.4",
                "elsewhere_off_seconds = 2.0",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.lamp.unheard_on_seconds == 0.12
    assert config.lamp.unheard_off_seconds == 0.18
    assert config.lamp.elsewhere_on_seconds == 0.4
    assert config.lamp.elsewhere_off_seconds == 2.0


def test_lamp_blink_timings_must_be_positive(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[lamp]\nunheard_off_seconds = 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unheard_off_seconds"):
        load_config(path)


def test_single_allowed_chat_is_the_family():
    config = AppConfig(telegram=TelegramConfig(allowed_chat_ids=(-100,), family_chat_id=None))
    assert resolved_family_chat_id(config) == -100


def test_family_outside_the_allowlist_is_rejected():
    config = AppConfig(telegram=TelegramConfig(allowed_chat_ids=(-100,), family_chat_id=-5))
    with pytest.raises(ValueError):
        resolved_family_chat_id(config)


def test_same_pin_for_button_and_lamp_is_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[button]\npin = 17\n[lamp]\nenabled = true\npin = 17\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_env_file_parsing_does_not_expand_or_keep_a_bot_prefix():
    parsed = parse_env_text(
        """
        # comment
        export TELEGRAM_BOT_TOKEN="bot12345:ABC"
        OTHER=leave$me
        """
    )
    assert parsed["TELEGRAM_BOT_TOKEN"] == "bot12345:ABC"
    assert parsed["OTHER"] == "leave$me"
    assert normalize_token("bot12345:ABC") == "12345:ABC"
    assert normalize_token("12345:ABC") == "12345:ABC"
    assert normalize_token("botanical") == "botanical"


def test_environment_beats_the_file(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=1:from-file\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "2:from-env")
    assert load_token(env_file) == "2:from-env"


def test_token_is_read_from_a_file_when_the_environment_is_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env_file = tmp_path / "env"
    env_file.write_text("TELEGRAM_BOT_TOKEN='bot42:abcdef'\n", encoding="utf-8")
    assert load_token(env_file) == "42:abcdef"


def test_dev_mode_avoids_an_unwritable_system_data_dir(monkeypatch):
    monkeypatch.setattr("messagebox.config._writable_dir", lambda _path: False)
    config = load_config(ROOT / "config.example.toml", dev=True)
    assert config.dev_mode is True
    assert config.paths.data_dir == Path("data")


def test_unreadable_cwd_config_falls_back_to_the_system_file(tmp_path, monkeypatch, capsys):
    cwd = tmp_path / "launch"
    cwd.mkdir()
    local = cwd / "config.toml"
    local.write_text("this file must not be opened\n", encoding="utf-8")
    system = tmp_path / "etc-message-box" / "config.toml"
    system.parent.mkdir()
    system.write_text((ROOT / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("messagebox.config.SYSTEM_CONFIG_PATH", system)
    real_open = Path.open

    def guarded_open(self, *args, **kwargs):
        if self.resolve() == local.resolve():
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    assert resolve_config_path(None, dev=False) == system
    assert resolve_config_path(None, dev=True) == system
    assert "Ignoring" not in capsys.readouterr().err
    assert load_config(system, dev=False).button.pin == 17


def test_unreadable_cwd_config_is_mentioned_when_no_fallback_exists(tmp_path, monkeypatch, capsys):
    cwd = tmp_path / "launch"
    cwd.mkdir()
    local = cwd / "config.toml"
    local.write_text("hidden\n", encoding="utf-8")
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("messagebox.config.SYSTEM_CONFIG_PATH", tmp_path / "missing.toml")
    real_open = Path.open

    def guarded_open(self, *args, **kwargs):
        if self.resolve() == local.resolve():
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    assert resolve_config_path(None, dev=False) is None
    err = capsys.readouterr().err
    assert "not readable" in err
    assert "config.toml" in err


def test_readable_cwd_config_is_still_preferred(tmp_path, monkeypatch):
    cwd = tmp_path / "project"
    cwd.mkdir()
    local = cwd / "config.toml"
    local.write_text(
        "[button]\npin = 4\n[lamp]\nenabled = false\npin = 26\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("messagebox.config.SYSTEM_CONFIG_PATH", tmp_path / "missing.toml")
    chosen = resolve_config_path(None, dev=False)
    assert chosen is not None
    assert chosen.resolve() == local.resolve()
    assert load_config(chosen, dev=False).button.pin == 4
    assert SYSTEM_CONFIG_PATH == Path("/etc/message-box/config.toml")


def test_mic_override_from_the_environment(monkeypatch):
    monkeypatch.setenv("MESSAGEBOX_MIC", "plughw:4,0")
    config = load_config(ROOT / "config.example.toml", dev=False)
    assert config.audio.mic_device == "plughw:4,0"


def _write(tmp_path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_two_cards_load_and_a_third_chat_is_rejected(tmp_path):
    config = load_config(
        _write(
            tmp_path,
            """
            [telegram]
            allowed_chat_ids = [-1000000000001, -1000000000002]
            family_chat_id = -1000000000001

            [[cards]]
            uid = "04:a1:b2:c3:d4:e5:f6"
            chat_id = -1000000000001
            announce = "German"

            [[cards]]
            uid = "04B1C2D3E4F506"
            chat_id = -1000000000002
            announce = "English\\nroom"
            announce_file = "/var/lib/message-box/clips/english.wav"
            """,
        )
    )
    assert config.cards[0].uid == "04A1B2C3D4E5F6"
    assert config.cards[0].chat_id == -1000000000001
    assert config.cards[0].announce == "German"
    assert config.cards[1].announce == "English room"
    assert config.cards[1].announce_file == Path("/var/lib/message-box/clips/english.wav")
    with pytest.raises(ValueError, match="not in allowed_chat_ids"):
        load_config(
            _write(
                tmp_path,
                """
                [telegram]
                allowed_chat_ids = [-1000000000001]
                [[cards]]
                uid = "04A1B2C3D4E5F6"
                chat_id = -1000000000002
                announce = "English"
                """,
            )
        )


def test_duplicate_and_bad_uids_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="more than once"):
        load_config(
            _write(
                tmp_path,
                """
                [telegram]
                allowed_chat_ids = [-1000000000001]
                [[cards]]
                uid = "04A1B2C3"
                chat_id = -1000000000001
                announce = "German"
                [[cards]]
                uid = "04:a1:b2:c3"
                chat_id = -1000000000001
                announce = "German again"
                """,
            )
        )
    with pytest.raises(ValueError, match="not hex"):
        load_config(
            _write(
                tmp_path,
                """
                [telegram]
                allowed_chat_ids = [-1000000000001]
                [[cards]]
                uid = "zzzzzzzz"
                chat_id = -1000000000001
                announce = "German"
                """,
            )
        )


def test_a_card_needs_words_or_a_file(tmp_path):
    with pytest.raises(ValueError, match="announce"):
        load_config(
            _write(
                tmp_path,
                """
                [telegram]
                allowed_chat_ids = [-1000000000001]
                [[cards]]
                uid = "04A1B2C3D4E5F6"
                chat_id = -1000000000001
                """,
            )
        )
