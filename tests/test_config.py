"""Config, token loading, and the family-chat allowlist rule."""

from pathlib import Path

import pytest

from messagebox.config import (
    AppConfig,
    TelegramConfig,
    load_config,
    load_token,
    normalize_token,
    parse_env_text,
    resolved_family_chat_id,
)

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_loads():
    config = load_config(ROOT / "config.example.toml", dev=False)
    assert config.button.pin == 17
    assert config.lamp.pin == 26
    assert config.lamp.enabled is True
    assert config.button.hold_seconds == 0.6
    assert config.button.min_record_seconds == 1.0
    assert config.button.max_record_seconds == 120
    assert config.telegram.allowed_chat_ids == ()
    assert config.telegram.family_chat_id is None
    assert config.audio.play_empty_tone is False
    assert config.paths.data_dir == Path("/var/lib/message-box")
    assert config.paths.env_file == Path("/etc/message-box/env")


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


def test_mic_override_from_the_environment(monkeypatch):
    monkeypatch.setenv("MESSAGEBOX_MIC", "plughw:4,0")
    config = load_config(ROOT / "config.example.toml", dev=False)
    assert config.audio.mic_device == "plughw:4,0"
