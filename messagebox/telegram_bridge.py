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

from messagebox.policy import authenticated_sender_id, should_accept_chat, should_accept_sender
from messagebox.queue_store import DiskQueue, Inbox

log = logging.getLogger("messagebox.telegram")

_AUDIO_SUFFIXES = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".flac", ".aac", ".mp4"}


class TelegramBridge:
    def __init__(
        self,
        token: str,
        allowed: set[int],
        inbox: DiskQueue | Inbox,
        on_queued: Callable[[], Awaitable[None]],
        download_dir: Path,
        on_title: Callable[[int, str], Awaitable[None]] | None = None,
        on_incoming_meta: Callable[[dict], Awaitable[None]] | None = None,
        allowed_senders: set[int] | None = None,
        allow_any_sender: bool = False,
        max_incoming_bytes: int = 8 * 1024 * 1024,
        max_incoming_seconds: float = 600,
    ) -> None:
        self.token = token
        self.allowed = set(allowed)
        self.allowed_senders = set(allowed_senders or ())
        self.allow_any_sender = bool(allow_any_sender)
        self.max_incoming_bytes = int(max_incoming_bytes)
        self.max_incoming_seconds = float(max_incoming_seconds)
        self.inbox = inbox
        self._on_queued = on_queued
        self.download_dir = download_dir
        self._on_title = on_title
        self._on_incoming_meta = on_incoming_meta
        self._ignored: set[int] = set()
        self._ignored_senders: set[tuple[int, int]] = set()

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
        # A second group so a title update does not swallow the voice note.
        app.add_handler(MessageHandler(filters.ALL, self.on_seen), group=1)
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

    def _accepts(self, chat_id: int, sender_id: int | None) -> bool:
        """Both the chat and the authenticated sender must pass."""
        if not should_accept_chat(chat_id, self.allowed):
            return False
        return should_accept_sender(
            sender_id,
            self.allowed_senders,
            allow_any_sender=self.allow_any_sender,
        )

    async def on_media(self, update, context) -> None:
        message = getattr(update, "effective_message", None)
        chat = getattr(update, "effective_chat", None)
        if message is None or chat is None:
            return
        chat_id = _int_id(getattr(chat, "id", None))
        sender_id = authenticated_sender_id(message)
        if chat_id is None or not should_accept_chat(chat_id, self.allowed):
            if chat_id is not None and chat_id not in self._ignored:
                self._ignored.add(chat_id)
                log.info("ignoring audio from chat %s (not on the allowlist)", chat_id)
            return
        if not should_accept_sender(
            sender_id,
            self.allowed_senders,
            allow_any_sender=self.allow_any_sender,
        ):
            key = (chat_id, sender_id or 0)
            if key not in self._ignored_senders:
                self._ignored_senders.add(key)
                log.info(
                    "ignoring audio in chat %s from sender %s (sender not approved)",
                    chat_id,
                    sender_id if sender_id is not None else "none",
                )
            return
        attachment, kind = _attachment(message)
        if attachment is None:
            return
        if not _within_limits(
            attachment,
            max_bytes=self.max_incoming_bytes,
            max_seconds=self.max_incoming_seconds,
        ):
            log.info("ignoring audio in chat %s (size or length)", chat_id)
            return
        message_id = _int_id(getattr(message, "message_id", None))
        if message_id is None or message_id < 0:
            log.info("ignoring audio in chat %s (bad message id)", chat_id)
            return
        # Authorization, size, and the message id are settled before any download.
        self.download_dir.mkdir(parents=True, exist_ok=True)
        dest: Path | None = None
        try:
            tg_file = await attachment.get_file()
            suffix = _safe_suffix(getattr(tg_file, "file_path", None))
            dest = safe_incoming_path(self.download_dir, chat_id, message_id, suffix)
            if dest is None:
                log.info("ignoring audio in chat %s (unsafe path)", chat_id)
                return
            await tg_file.download_to_drive(custom_path=str(dest))
            size = dest.stat().st_size if dest.is_file() else 0
            if size <= 0 or size > self.max_incoming_bytes:
                log.info("ignoring audio in chat %s (downloaded size)", chat_id)
                return
            user = getattr(message, "from_user", None)
            label = None
            if user is not None:
                label = getattr(user, "first_name", None) or getattr(user, "username", None)
            item = self.inbox.enqueue(
                dest,
                {
                    "source_chat_id": chat_id,
                    "kind": kind,
                    "sender_id": sender_id,
                    "sender_name": label,
                    "sender_label": label,
                    "created_at": time.time(),
                    "duration": _duration_value(attachment),
                },
            )
        except Exception as exc:
            log.warning("could not save audio from chat %s (%s)", chat_id, type(exc).__name__)
            return
        finally:
            if dest is not None:
                dest.unlink(missing_ok=True)
        log.info(
            "queued %s from chat %s as %s (%s waiting)",
            kind,
            chat_id,
            item.id,
            len(self.inbox),
        )
        await self._on_queued()
        if self._on_incoming_meta is not None:
            try:
                await self._on_incoming_meta(dict(item.meta))
            except Exception:
                log.exception("could not prepare the sender introduction")
        await self._note_title(chat)

    async def on_seen(self, update, context) -> None:
        """Remember a group title from an approved sender. Does not reply."""
        message = getattr(update, "effective_message", None)
        chat = getattr(update, "effective_chat", None)
        if message is None or chat is None:
            return
        chat_id = _int_id(getattr(chat, "id", None))
        if chat_id is None or not self._accepts(chat_id, authenticated_sender_id(message)):
            return
        await self._note_title(chat)

    async def _note_title(self, chat) -> None:
        if self._on_title is None:
            return
        if not should_accept_chat(chat.id, self.allowed):
            return
        title = getattr(chat, "title", None)
        if not title:
            return
        try:
            await self._on_title(int(chat.id), str(title))
        except Exception:
            log.exception("could not save a chat title")

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


def _int_id(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _safe_suffix(file_path: Any) -> str:
    """Extension of the basename only. The Telegram path is never opened locally."""
    name = Path(str(file_path or "")).name
    suffix = Path(name).suffix.lower()
    if suffix in _AUDIO_SUFFIXES and "/" not in suffix and "\\" not in suffix:
        return suffix
    return ".ogg"


def safe_incoming_path(download_dir: Path, chat_id: int, message_id: int, suffix: str) -> Path | None:
    """A file under ``download_dir`` built only from checked integers."""
    if _int_id(chat_id) is None or _int_id(message_id) is None or message_id < 0:
        return None
    if suffix not in _AUDIO_SUFFIXES:
        return None
    root = Path(download_dir).resolve()
    name = f"incoming-{chat_id}-{message_id}{suffix}"
    if name != Path(name).name:
        return None
    dest = (root / name).resolve()
    try:
        dest.relative_to(root)
    except ValueError:
        return None
    return dest


def _file_size(attachment) -> int | None:
    size = getattr(attachment, "file_size", None)
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        return None
    return size


def _duration_value(attachment) -> float | None:
    raw = getattr(attachment, "duration", None)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value < 0:
        return None
    return value


def _within_limits(attachment, *, max_bytes: int, max_seconds: float) -> bool:
    size = _file_size(attachment)
    if size is None or size > max_bytes:
        return False
    duration = _duration_value(attachment)
    if duration is None:
        raw = getattr(attachment, "duration", None)
        if raw is not None:
            return False
    elif duration > max_seconds:
        return False
    return True


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
