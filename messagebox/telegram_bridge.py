"""Telegram polling, allowlist, and voice-note delivery.

Incoming voice notes and audio files from other chats are ignored.
``/chatid`` answers in any chat so a new box can be set up; it only
repeats that chat's own id.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from messagebox.policy import should_accept_chat
from messagebox.queue_store import DiskQueue

log = logging.getLogger("messagebox.telegram")

_AUDIO_SUFFIXES = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".flac", ".aac", ".mp4"}


class TelegramBridge:
    def __init__(
        self,
        token: str,
        allowed: set[int],
        inbox: DiskQueue,
        on_queued: Callable[[], Awaitable[None]],
        download_dir: Path,
    ) -> None:
        self.token = token
        self.allowed = set(allowed)
        self.inbox = inbox
        self._on_queued = on_queued
        self.download_dir = download_dir
        self._ignored: set[int] = set()

    def build(self):
        from telegram.ext import Application, CommandHandler, MessageHandler, filters
        from telegram.request import HTTPXRequest

        request = HTTPXRequest(connect_timeout=15.0, read_timeout=30.0, write_timeout=60.0)
        updates = HTTPXRequest(connect_timeout=15.0, read_timeout=45.0)
        app = (
            Application.builder()
            .token(self.token)
            .request(request)
            .get_updates_request(updates)
            .job_queue(None)
            .build()
        )
        app.add_handler(CommandHandler("chatid", self.on_chatid))
        app.add_handler(CommandHandler("start", self.on_start))
        app.add_handler(MessageHandler(_media_filter(filters), self.on_media))
        app.add_error_handler(self.on_error)
        return app

    async def on_start(self, update, context) -> None:
        message = update.effective_message
        if message is None:
            return
        await message.reply_text(
            "Message Box is running. Send /chatid in the family chat to get its id."
        )

    async def on_chatid(self, update, context) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return
        await message.reply_text(
            f"Chat id:\n`{chat.id}`\n\n"
            "Add that number to allowed_chat_ids and family_chat_id in the config, "
            "then restart Message Box.",
            parse_mode="Markdown",
        )

    async def on_media(self, update, context) -> None:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if message is None or chat is None:
            return
        if user is not None and user.is_bot:
            return
        if not should_accept_chat(chat.id, self.allowed):
            if chat.id not in self._ignored:
                self._ignored.add(chat.id)
                log.info("ignoring audio from chat %s (not on the allowlist)", chat.id)
            return
        attachment, kind = _attachment(message)
        if attachment is None:
            return
        self.download_dir.mkdir(parents=True, exist_ok=True)
        dest = self.download_dir / f"incoming-{chat.id}-{message.message_id}"
        try:
            tg_file = await attachment.get_file()
            suffix = Path(tg_file.file_path or "").suffix.lower()
            if suffix not in _AUDIO_SUFFIXES:
                suffix = ".ogg"
            dest = dest.with_suffix(suffix)
            await tg_file.download_to_drive(custom_path=str(dest))
            label = None
            if user is not None:
                label = user.first_name or user.username
            item = self.inbox.enqueue(
                dest,
                {
                    "source_chat_id": chat.id,
                    "kind": kind,
                    "sender_label": label,
                    "created_at": time.time(),
                    "duration": getattr(attachment, "duration", None),
                },
            )
        except Exception as exc:
            log.warning("could not save audio from chat %s (%s)", chat.id, type(exc).__name__)
            return
        finally:
            dest.unlink(missing_ok=True)
        log.info(
            "queued %s from chat %s as %s (%s waiting)",
            kind,
            chat.id,
            item.id,
            len(self.inbox),
        )
        await self._on_queued()

    async def on_error(self, update, context) -> None:
        err = context.error
        # The exception text can contain the request URL, which contains the
        # token. Log the type only; the logging filter is a second line of defence.
        log.error("telegram error: %s", type(err).__name__ if err else "unknown")


def _media_filter(filters):
    media = filters.VOICE | filters.AUDIO
    document_audio = getattr(filters.Document, "AUDIO", None)
    if document_audio is not None:
        return media | document_audio
    return media


def _attachment(message) -> tuple[Any, str]:
    if message.voice is not None:
        return message.voice, "voice"
    if message.audio is not None:
        return message.audio, "audio"
    document = message.document
    if document is not None and (document.mime_type or "").startswith("audio/"):
        return document, "audio"
    return None, ""


async def send_voice_note(bot, chat_id: int, path: Path, duration: float | None) -> None:
    """Upload an OGG/Opus file as a Telegram voice note."""
    from telegram import InputFile

    payload = path.read_bytes()
    kwargs: dict[str, Any] = {}
    if duration is not None and duration >= 1:
        kwargs["duration"] = int(duration)
    await bot.send_voice(
        chat_id=chat_id,
        voice=InputFile(payload, filename="note.ogg"),
        **kwargs,
    )
