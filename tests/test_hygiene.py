"""The public repo must not be able to swallow a bot token by accident."""

import logging
from io import StringIO
from pathlib import Path

from messagebox.logging_setup import RedactSecrets

ROOT = Path(__file__).resolve().parents[1]


def test_secret_files_are_gitignored_and_examples_are_empty():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore
    assert "config.toml" in ignore
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    token_lines = [line for line in example.splitlines() if line.startswith("TELEGRAM_BOT_TOKEN=")]
    assert token_lines == ["TELEGRAM_BOT_TOKEN="]
    config = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert "allowed_chat_ids = []" in config
    assert "TELEGRAM_BOT_TOKEN" not in config


def test_log_lines_do_not_keep_the_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "12345:SECRET")
    logger = logging.getLogger("messagebox.test.redact")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactSecrets())
    logger.addHandler(handler)
    logger.info("request failed for %s", "12345:SECRET")
    text = stream.getvalue()
    assert "SECRET" not in text
    assert "***" in text


def test_install_and_service_match_the_pi_setup():
    script = (ROOT / "install.sh").read_text(encoding="utf-8")
    for package in ("ffmpeg", "alsa-utils", "python3-venv", "python3-lgpio", "espeak-ng", "i2c-tools"):
        assert package in script
    assert "chmod 600" in script
    assert "groupadd --system i2c" in script
    assert script.index("groupadd --system i2c") < script.index("systemctl enable")
    assert "dtparam=i2c_arm_baudrate=10000" in script
    assert 'baud_value="${baud_value%%[^0-9]*}"' in script
    service = (ROOT / "deploy/message-box.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/message-box/env" in service
    assert "User=messagebox" in service
    assert "python -m messagebox run" in service
    assert "StandardOutput=journal" in service
    assert "WorkingDirectory=/var/lib/message-box" in service
    assert "Environment=LG_WD=/var/lib/message-box" in service
    assert "LG_WD=/var/lib/message-box" in script
    groups = service.split("SupplementaryGroups=", 1)[1].splitlines()[0]
    assert "i2c" in groups.split()


def test_bridge_builds_on_telegram_21(tmp_path):
    from messagebox.queue_store import DiskQueue
    from messagebox.telegram_bridge import TelegramBridge

    async def _queued() -> None:
        return None

    bridge = TelegramBridge(
        token="123456:TEST",
        allowed={-100},
        inbox=DiskQueue(tmp_path / "inbox"),
        on_queued=_queued,
        download_dir=tmp_path / "tmp",
    )
    app = bridge.build()
    commands: set[str] = set()
    for group in app.handlers.values():
        for handler in group:
            commands.update(getattr(handler, "commands", set()))
    assert "chatid" in commands
    assert "start" in commands
    assert app.updater is not None


def test_service_does_not_revert_cards_on_idle():
    runtime = (ROOT / "messagebox/runtime.py").read_text(encoding="utf-8")
    assert "revert_if_idle" not in runtime


def test_readme_uses_physical_pin_numbers():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "physical pin 11" in readme
    assert "physical pin 9" in readme
    assert "physical pin 37" in readme
    assert "physical pin 17" in readme
