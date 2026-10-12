"""Message Box command line.

    message-box                 run the box (or the systemd service)
    message-box --dev           keyboard instead of GPIO, for a laptop
    message-box --check-audio   beep, record, play back
    message-box chatid          list chats that have talked to the bot
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tomllib
from pathlib import Path

from messagebox import __version__
from messagebox.chatid import print_chat_ids
from messagebox.config import load_config, resolve_config_path
from messagebox.logging_setup import setup_logging
from messagebox.runtime import Runtime, token_from_config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="message-box",
        description="One-button voice notes for a child, via Telegram.",
    )
    parser.add_argument("--version", action="version", version=f"message-box {__version__}")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="run without GPIO; the keyboard is the button (see the README)",
    )
    parser.add_argument("--config", type=Path, help="path to config.toml")
    parser.add_argument(
        "--check-audio",
        action="store_true",
        help="find the USB mic and speaker, play a beep, record 2 seconds, play it back",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=("run", "chatid"),
        help="run the box, or print chat ids for setup (default: run)",
    )
    args = parser.parse_args(argv)
    try:
        path = resolve_config_path(args.config, dev=args.dev or args.check_audio)
        config = load_config(path, dev=args.dev)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    setup_logging(config.log_level)

    if args.command == "chatid":
        token = token_from_config(config)
        if not token:
            print(
                "TELEGRAM_BOT_TOKEN is not set. Put it in /etc/message-box/env "
                "or in .env for dev mode.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        os.environ["TELEGRAM_BOT_TOKEN"] = token
        raise SystemExit(asyncio.run(print_chat_ids(token)))

    runtime = Runtime(config)
    if args.check_audio:
        raise SystemExit(asyncio.run(runtime.check_audio()))

    token = token_from_config(config)
    if not token:
        print(
            "TELEGRAM_BOT_TOKEN is not set.\n"
            "On the Pi, edit /etc/message-box/env (see the README).\n"
            "On a laptop, copy .env.example to .env.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    runtime.token = token
    try:
        asyncio.run(runtime.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
