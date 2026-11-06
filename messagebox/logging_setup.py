"""Logging to stderr, which systemd forwards to the journal.

A filter strips the bot token if a library ever puts the request URL in a
log line. ``journalctl -u message-box -f`` shows the same text.
"""

from __future__ import annotations

import logging
import os
import sys


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            return True
        record.msg = _scrub(record.msg, token)
        if isinstance(record.args, dict):
            record.args = {key: _scrub(value, token) for key, value in record.args.items()}
        elif record.args:
            record.args = tuple(_scrub(arg, token) for arg in record.args)
        if record.exc_text:
            record.exc_text = _scrub(record.exc_text, token)
        return True


class RedactingFormatter(logging.Formatter):
    """Scrub the finished line, including a traceback added during format."""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if token and token in text:
            return text.replace(token, "***")
        return text


def _scrub(value, token: str):
    if isinstance(value, str) and token in value:
        return value.replace(token, "***")
    return value


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactSecrets())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
