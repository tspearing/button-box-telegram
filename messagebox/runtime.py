"""Runtime: button, lamp, microphone, speaker, and Telegram.

The button loop stays short. Recording and playback are separate tasks so a
message that is playing can be interrupted by a hold, and a hold is never
interrupted by a message that arrives.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from pathlib import Path

from messagebox import __version__
from messagebox.announce import Announcer, espeak_ng_synth
from messagebox.audio import AudioEngine, ffmpeg_available
from messagebox.button_machine import ButtonMachine
from messagebox.cards import build_directory
from messagebox.config import AppConfig, load_token, resolved_family_chat_id
from messagebox.coordinator import Coordinator, EffectKind
from messagebox.inputs.base import ButtonEdge
from messagebox.inputs.gpio_button import GpioButtonSource
from messagebox.inputs.keyboard import KeyboardButtonSource
from messagebox.inputs.pn532 import Pn532CardSource
from messagebox.lamp import LampPatterns, open_lamp
from messagebox.policy import should_send_to
from messagebox.queue_store import DiskQueue
from messagebox.recipients import RecipientDirectory, normalize_uid
from messagebox.retry import retry_delay
from messagebox.selection import load_selection, save_selection
from messagebox.sounds import SoundBank
from messagebox.telegram_bridge import TelegramBridge, send_voice_note
from messagebox.workdir import absolute_data_dir, ensure_writable_data_dir, prepare_lgpio_directory

log = logging.getLogger("messagebox.runtime")


class Runtime:
    def __init__(self, config: AppConfig, token: str = "") -> None:
        self.config = config
        self.token = token
        self.audio = AudioEngine(config.audio)
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._edges: asyncio.Queue = asyncio.Queue()
        self._jobs: asyncio.Queue = asyncio.Queue()
        self._outbox_wake = asyncio.Event()
        self._coord_lock = asyncio.Lock()
        self._capture_lock = asyncio.Lock()
        self._speaker_lock = asyncio.Lock()
        self._source = None
        self._lamp = None
        self._bot = None
        self._inbox: DiskQueue | None = None
        self._outbox: DiskQueue | None = None
        self._coord: Coordinator | None = None
        self._directory: RecipientDirectory | None = None
        self._sounds: SoundBank | None = None
        self._announcer: Announcer | None = None
        self._selection_path: Path | None = None
        self._cards: asyncio.Queue = asyncio.Queue()
        self._card_source: Pn532CardSource | None = None
        self._capture_proc = None
        self._capture_path: Path | None = None
        self._capture_chat_id: int | None = None
        self._capturing = False
        self._play_proc = None
        self._play_token = 0
        self._background: set[asyncio.Task] = set()
        self._deferred: list[str] = []
        self._was_recording = False
        self._allowed: set[int] = set(config.telegram.allowed_chat_ids)

    async def check_audio(self) -> int:
        """Play a beep, record two seconds, and play them back. No Telegram."""
        if not ffmpeg_available():
            print("ffmpeg is not installed. On the Pi: sudo apt install ffmpeg")
            return 1
        preferred = self.config.paths.data_dir
        data = ensure_writable_data_dir(preferred)
        if data != absolute_data_dir(preferred):
            print(f"Cannot write to {preferred}. Using {data}")
        print(f"Audio check files: {data}")
        notes = self.audio.fallback_notes()
        for note in notes:
            print(f"Warning: {note}")
        sounds = SoundBank(data / "sounds")
        sounds.ensure()
        print(self.audio.describe())
        print("\nPlaying the start beep...")
        if not await self.audio.play_and_wait(sounds.path("start")):
            print("Could not play through the speaker. See the README troubleshooting section.")
            return 1
        dest = data / "tmp" / "check.ogg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.unlink(missing_ok=True)
        print("Recording 2 seconds. Say something into the microphone...")
        proc = await self.audio.start_recording(dest, max_seconds=2)
        await proc.wait()
        if not dest.is_file() or dest.stat().st_size < 500:
            print("Recording failed. Check the microphone and mic_device in the config.")
            return 1
        print("Playing the recording back...")
        ok = await self.audio.play_and_wait(dest)
        if not ok:
            print("Playback of the recording failed.")
            return 1
        if notes:
            print("Audio check finished, with the warnings above. Playback may have been silent.")
            return 0
        print("Audio check passed.")
        return 0

    async def run(self) -> None:
        if not ffmpeg_available():
            log.error("ffmpeg is not installed")
            raise SystemExit(1)
        if not self.token:
            log.error("TELEGRAM_BOT_TOKEN is not set")
            raise SystemExit(1)
        try:
            family_id = resolved_family_chat_id(self.config)
        except ValueError as exc:
            log.error("%s", exc)
            raise SystemExit(1) from exc

        preferred = self.config.paths.data_dir
        data = ensure_writable_data_dir(preferred)
        if data != absolute_data_dir(preferred):
            log.warning("cannot write %s; storing notes in %s", preferred, data)
        self.config = replace(self.config, paths=replace(self.config.paths, data_dir=data))
        if not self.config.dev_mode:
            # Before gpiozero/lgpio. Dev mode never opens the GPIO chip.
            prepare_lgpio_directory(data)
        (data / "tmp").mkdir(parents=True, exist_ok=True)
        self._inbox = DiskQueue(data / "inbox")
        self._outbox = DiskQueue(data / "outbox")
        self._sounds = SoundBank(data / "sounds")
        self._sounds.ensure()
        self._announcer = Announcer(data / "announce-cache", espeak_ng_synth)
        self._selection_path = data / "selection.json"
        self._directory = build_directory(
            family_id or 0,
            self.config.cards,
            self.config.recipient.idle_timeout_seconds,
        )
        self._restore_selection()
        machine = ButtonMachine(
            hold_seconds=self.config.button.hold_seconds,
            min_record_seconds=self.config.button.min_record_seconds,
            max_record_seconds=self.config.button.max_record_seconds,
            debounce_seconds=self.config.button.debounce_seconds,
        )
        self._coord = Coordinator(
            machine,
            self._inbox,
            play_empty_tone=self.config.audio.play_empty_tone,
        )
        patterns = LampPatterns(
            waiting=self.config.lamp.waiting,
            waiting_on_seconds=self.config.lamp.waiting_on_seconds,
            waiting_off_seconds=self.config.lamp.waiting_off_seconds,
            recording_on_seconds=self.config.lamp.recording_on_seconds,
            recording_off_seconds=self.config.lamp.recording_off_seconds,
        )
        self._lamp = open_lamp(
            enabled=self.config.lamp.enabled,
            pin=self.config.lamp.pin,
            active_high=self.config.lamp.active_high,
            patterns=patterns,
            dev=self.config.dev_mode,
        )
        for effect in self._coord.boot():
            self._show(effect)

        log.info("Message Box %s", __version__)
        log.info("data directory: %s", data)
        log.info("inbox: %s waiting", len(self._inbox))
        if self.config.cards:
            log.info("%s card(s) configured", len(self.config.cards))
            for card in self.config.cards:
                log.info(
                    "card %s speaks %r and sends to chat %s",
                    card.uid,
                    card.announce or "Card",
                    card.chat_id,
                )
        else:
            log.info("no NFC cards configured; recordings use the default chat")
        active = self._directory.current
        if active.chat_id and should_send_to(active.chat_id, self._allowed):
            log.info("sending to chat %s (%s)", active.chat_id, active.display_name)
        else:
            log.warning(
                "family chat is not ready. Incoming notes are ignored and "
                "recordings stay in the outbox until allowed_chat_ids and "
                "family_chat_id are set. Send /chatid to the bot."
            )
        log.info("audio:\n%s", self.audio.describe())

        bridge = TelegramBridge(
            token=self.token,
            allowed=self._allowed,
            inbox=self._inbox,
            on_queued=self._on_incoming,
            download_dir=data / "tmp",
        )
        application = bridge.build()
        self._loop = asyncio.get_running_loop()
        self._install_signals()
        self._source = self._open_button()
        try:
            self._source.start(self._on_edge)
        except SystemExit:
            if self._lamp is not None:
                self._lamp.close()
            raise
        except Exception as exc:
            log.error("button input failed: %s", exc)
            if self.config.dev_mode:
                log.error("Dev mode needs a real terminal (not a pipe).")
            else:
                log.error(
                    "On a laptop, run with --dev. On the Pi, install lgpio "
                    "(install.sh does this) and make sure the messagebox user is in the gpio group."
                )
            if self._lamp is not None:
                self._lamp.close()
            raise SystemExit(1) from exc

        self._start_cards()
        tasks = [
            asyncio.create_task(self._button_loop(), name="button"),
            asyncio.create_task(self._audio_loop(), name="audio"),
            asyncio.create_task(self._outbox_loop(), name="outbox"),
            asyncio.create_task(self._card_loop(), name="cards"),
            asyncio.create_task(self._poll(application), name="telegram"),
        ]
        poll_task = tasks[-1]
        try:
            await self._stop.wait()
        finally:
            for task in tasks:
                if task is not poll_task:
                    task.cancel()
            await asyncio.gather(
                *(task for task in tasks if task is not poll_task),
                return_exceptions=True,
            )
            for task in list(self._background):
                task.cancel()
            if self._background:
                await asyncio.gather(*self._background, return_exceptions=True)
            await self._shutdown_audio()
            if self._card_source is not None:
                self._card_source.stop()
                self._card_source = None
            if self._source is not None:
                self._source.stop()
            try:
                await asyncio.wait_for(poll_task, timeout=8)
            except TimeoutError:
                poll_task.cancel()
                await asyncio.gather(poll_task, return_exceptions=True)
            if self._lamp is not None:
                self._lamp.close()
            log.info("stopped")

    def _open_button(self):
        if self.config.dev_mode:
            log.info("dev mode: keyboard is the button")
            return KeyboardButtonSource(
                window=self.config.dev_keys.repeat_window_seconds,
                gap=self.config.dev_keys.repeat_gap_seconds,
            )
        return GpioButtonSource(
            self.config.button.pin,
            bounce_seconds=self.config.button.bounce_seconds,
        )

    def _install_signals(self) -> None:
        import signal

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:
                pass

    def _on_edge(self, edge: ButtonEdge, at: float) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._edges.put_nowait, (edge, at))

    def _on_card(self, uid: str) -> None:
        """NFC thread entry. Only queues the UID. It does not touch the button."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._cards.put_nowait, uid)

    def _start_cards(self) -> None:
        if self.config.dev_mode:
            log.info("dev mode: NFC reader is not opened")
            return
        if not self.config.nfc.enabled:
            log.info("NFC reader is disabled in the config")
            return
        self._card_source = Pn532CardSource(
            bus=self.config.nfc.i2c_bus,
            address=self.config.nfc.i2c_address,
        )
        try:
            self._card_source.start(self._on_card)
        except Exception:
            log.exception("NFC reader thread did not start; continuing without cards")
            self._card_source = None

    def _restore_selection(self) -> None:
        assert self._directory is not None and self._selection_path is not None
        saved = load_selection(self._selection_path)
        if saved is None:
            return
        uid, _chat_id = saved
        chosen = self._directory.select_card(uid, time.monotonic())
        if chosen is None:
            log.info("saved card %s is not configured; using the default chat", uid)
            return
        log.info(
            "restored card %s, sending to chat %s (%s)",
            uid,
            chosen.chat_id,
            chosen.display_name,
        )

    async def _poll(self, application) -> None:
        try:
            async with application:
                self._bot = application.bot
                await application.start()
                if application.updater is None:
                    log.error("telegram updater is missing")
                    return
                await application.updater.start_polling(
                    bootstrap_retries=-1,
                    allowed_updates=["message"],
                    drop_pending_updates=False,
                    poll_interval=0.0,
                )
                log.info("telegram polling started")
                await self._stop.wait()
                await application.updater.stop()
                await application.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("telegram polling stopped")
            self._stop.set()
        finally:
            self._bot = None

    async def _button_loop(self) -> None:
        assert self._coord is not None
        while not self._stop.is_set():
            if isinstance(self._source, KeyboardButtonSource) and self._source.quit_requested:
                self._stop.set()
                return
            events: list[tuple[ButtonEdge, float]] = []
            try:
                events.append(await asyncio.wait_for(self._edges.get(), timeout=0.05))
            except TimeoutError:
                pass
            while True:
                try:
                    events.append(self._edges.get_nowait())
                except asyncio.QueueEmpty:
                    break
            async with self._coord_lock:
                effects = []
                for edge, at in events:
                    log.info("button %s", edge.value)
                    if edge is ButtonEdge.PRESS:
                        effects.extend(self._coord.press(at))
                    else:
                        effects.extend(self._coord.release(at))
                effects.extend(self._coord.tick(time.monotonic()))
                recording = self._coord.recording
            self._dispatch(effects)
            if self._was_recording and not recording:
                self._jobs.put_nowait(self.flush_deferred)
            self._was_recording = recording

    def _dispatch(self, effects) -> None:
        for effect in effects:
            if effect.kind is EffectKind.LAMP:
                self._show(effect)
            elif effect.kind is EffectKind.START_RECORD:
                self._jobs.put_nowait(self.begin_capture)
            elif effect.kind is EffectKind.STOP_RECORD:
                self._jobs.put_nowait(_job(self.finish_capture, True, effect.recorded_for))
            elif effect.kind is EffectKind.DISCARD_RECORD:
                self._jobs.put_nowait(_job(self.finish_capture, False, effect.recorded_for))
            elif effect.kind is EffectKind.PLAY and effect.item_id:
                self._spawn(self.begin_playback(effect.item_id))
            elif effect.kind is EffectKind.EMPTY:
                self._spawn(self.play_cue("empty"))

    def _show(self, effect) -> None:
        if self._lamp is not None and effect.lamp:
            self._lamp.set_mode(effect.lamp)

    async def _audio_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = await asyncio.wait_for(self._jobs.get(), timeout=0.5)
            except TimeoutError:
                continue
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("audio job failed")

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(self._guard(coro))
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def _guard(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("background task failed")

    async def begin_capture(self) -> None:
        assert self._sounds is not None
        async with self._capture_lock:
            if self._capturing:
                return
            if self._directory is not None:
                # Snapshot before the beep, so a card tapped during this
                # recording does not retarget it.
                self._capture_chat_id = self._directory.current.chat_id
            self._interrupt_playback()
            async with self._speaker_lock:
                await self.audio.play_and_wait(self._sounds.path("start"))
                path = self.config.paths.data_dir / "tmp" / "live.ogg"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.unlink(missing_ok=True)
                try:
                    self._capture_proc = await self.audio.start_recording(
                        path,
                        max_seconds=self.config.button.max_record_seconds + 1,
                    )
                except Exception:
                    log.exception("could not start the microphone")
                    await self.audio.play_and_wait(self._sounds.path("error"))
                    return
                self._capture_path = path
                self._capturing = True
                log.info("recording")

    async def finish_capture(self, send: bool, duration: float) -> None:
        assert self._outbox is not None and self._directory is not None and self._sounds is not None
        async with self._capture_lock:
            proc = self._capture_proc
            path = self._capture_path
            was = self._capturing
            self._capture_proc = None
            self._capture_path = None
            self._capturing = False
            ok = False
            if was and proc is not None and path is not None:
                ok = await self.audio.stop_recording(proc, path)
            chat_id = self._capture_chat_id
            self._capture_chat_id = None
            cue: str | None
            if send and ok and path is not None and path.is_file():
                self._enqueue_outgoing(path, duration, chat_id)
                path.unlink(missing_ok=True)
                self._outbox_wake.set()
                cue = None
                log.info("recording stored for sending (%.1fs)", duration)
            else:
                if path is not None:
                    path.unlink(missing_ok=True)
                cue = "error" if send else "discard"
                if send:
                    log.error("recording was not usable and was not sent")
                else:
                    log.info("recording discarded (%.1fs of audio)", duration)
        # A discard that never opened the mic still gets a tone. A failed send
        # whose capture never started already played its error tone in begin_capture.
        if cue == "discard" or (cue == "error" and was):
            await self.play_cue(cue)
        await self.flush_deferred()

    def _enqueue_outgoing(self, path: Path, duration: float, chat_id: int | None = None) -> None:
        assert self._outbox is not None and self._directory is not None
        if chat_id is None:
            chat_id = self._directory.current.chat_id
        target = chat_id if should_send_to(chat_id, self._allowed) else None
        if target is None:
            log.warning("no allowed family chat yet; the note stays in the outbox")
        self._directory.note_activity(time.monotonic())
        self._outbox.enqueue(
            path,
            {
                "target_chat_id": target,
                "duration": duration,
                "created_at": time.time(),
                "attempts": 0,
            },
        )

    async def begin_playback(self, item_id: str) -> None:
        assert self._inbox is not None and self._coord is not None
        item = self._inbox.get(item_id)
        if item is None or not item.path.is_file():
            log.error("queued note %s is missing; dropping it", item_id)
            async with self._coord_lock:
                effects = self._coord.playback_finished(item_id, success=True)
            self._dispatch(effects)
            return
        self._play_token += 1
        token = self._play_token
        await self._play_item(item, token)

    async def _play_item(self, item, token: int) -> None:
        assert self._coord is not None and self._inbox is not None
        success = False
        try:
            async with self._speaker_lock:
                if token != self._play_token:
                    success = False
                else:
                    proc = await self.audio.play(item.path)
                    self._play_proc = proc
                    code = await proc.wait()
                    if self._play_proc is proc:
                        self._play_proc = None
                    success = code == 0 and token == self._play_token
                    if not success and token == self._play_token:
                        log.error("playback of %s failed (ffmpeg %s)", item.id, code)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("playback crashed")
            success = False
        if token != self._play_token:
            success = False
        async with self._coord_lock:
            effects = self._coord.playback_finished(item.id, success=success)
        self._dispatch(effects)
        if success:
            assert self._directory is not None
            self._directory.note_activity(time.monotonic())
            log.info("played %s, %s still waiting", item.id, len(self._inbox))

    def _interrupt_playback(self) -> None:
        self._play_token += 1
        proc = self._play_proc
        if proc is not None and proc.returncode is None:
            proc.terminate()

    async def play_cue(self, name: str) -> None:
        assert self._coord is not None
        if name == "sent" and (self._coord.recording or self._capturing):
            self._deferred.append(name)
            return
        await self._play_cue_now(name)

    async def _play_cue_now(self, name: str) -> None:
        assert self._sounds is not None
        async with self._speaker_lock:
            if self._capturing:
                if name == "sent":
                    self._deferred.append(name)
                return
            await self.audio.play_and_wait(self._sounds.path(name))

    async def flush_deferred(self) -> None:
        assert self._coord is not None
        while self._deferred and not self._coord.recording and not self._capturing:
            name = self._deferred.pop(0)
            await self._play_cue_now(name)

    async def _on_incoming(self) -> None:
        assert self._coord is not None
        async with self._coord_lock:
            effects = self._coord.message_enqueued()
        self._dispatch(effects)

    async def _outbox_loop(self) -> None:
        assert self._outbox is not None and self._directory is not None
        while not self._stop.is_set():
            state, item = self._outbox.head_state(time.time())
            if state == "empty" or item is None:
                self._outbox_wake.clear()
                if self._outbox.peek() is None:
                    try:
                        await asyncio.wait_for(self._outbox_wake.wait(), timeout=2)
                    except TimeoutError:
                        pass
                continue
            if state == "wait":
                due = float(item.meta.get("next_attempt_at") or 0)
                await asyncio.sleep(min(2.0, max(0.2, due - time.time())))
                continue
            target = item.meta.get("target_chat_id")
            if not target:
                chat_id = self._directory.current.chat_id
                if should_send_to(chat_id, self._allowed):
                    item = self._outbox.update(item.id, target_chat_id=chat_id)
                    target = chat_id
                else:
                    log.warning("holding outgoing note %s until the family chat is configured", item.id)
                    self._outbox.update(item.id, next_attempt_at=time.time() + 15)
                    await asyncio.sleep(1)
                    continue
            target = int(target)
            if not should_send_to(target, self._allowed):
                log.error("not sending %s to chat %s; it is not on the allowlist", item.id, target)
                self._outbox.update(item.id, next_attempt_at=time.time() + 300)
                await asyncio.sleep(1)
                continue
            attempts = int(item.meta.get("attempts") or 0) + 1
            try:
                await self._send(target, item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                delay = _failure_delay(exc, attempts)
                self._outbox.update(
                    item.id,
                    attempts=attempts,
                    next_attempt_at=time.time() + delay,
                )
                log.warning(
                    "sending %s failed (%s); retry in %ss",
                    item.id,
                    type(exc).__name__,
                    int(delay),
                )
            else:
                self._outbox.remove(item.id)
                log.info("sent %s to chat %s", item.id, target)
                self._directory.note_activity(time.monotonic())
                self._spawn(self.play_cue("sent"))

    async def _send(self, chat_id: int, item) -> None:
        bot = self._bot
        if bot is None:
            raise ConnectionError("telegram is not connected yet")
        duration = item.meta.get("duration")
        await send_voice_note(
            bot,
            chat_id,
            item.path,
            float(duration) if duration else None,
        )

    async def _card_loop(self) -> None:
        """Apply taps queued by the NFC thread. Does not poll I2C itself."""
        assert self._directory is not None
        while not self._stop.is_set():
            try:
                uid = await asyncio.wait_for(self._cards.get(), timeout=0.5)
            except TimeoutError:
                continue
            try:
                await self._handle_card(uid)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("card handling failed")

    async def _handle_card(self, uid: str) -> None:
        assert self._directory is not None
        try:
            shown = normalize_uid(uid)
        except ValueError:
            shown = uid
        chosen = self._directory.select_card(uid, time.monotonic())
        if chosen is None:
            log.info("unknown card %s", shown)
            if self._sounds is not None:
                self._spawn(self._play_speech(self._sounds.path("error")))
            return
        log.info("card %s selected chat %s (%s)", shown, chosen.chat_id, chosen.display_name)
        if self._selection_path is not None:
            try:
                save_selection(self._selection_path, chat_id=chosen.chat_id, uid=shown)
            except OSError as exc:
                log.warning("could not save the selected card: %s", exc)
        self._spawn(self._announce(chosen))

    async def _announce(self, recipient) -> None:
        if self._announcer is None:
            return
        try:
            # espeak-ng is a blocking process. Keep it off the event loop so
            # the button, the microphone, and Telegram keep running.
            clip = await asyncio.to_thread(
                self._announcer.clip_for,
                recipient.display_name,
                recipient.announce_clip,
            )
        except Exception:
            log.exception("could not prepare the announcement")
            return
        if clip is None:
            log.info("no announcement audio for %s", recipient.display_name)
            return
        await self._play_speech(clip)

    def _speech_blocked(self) -> bool:
        """True while a recording or a queued note owns the speaker."""
        if self._capturing:
            return True
        coord = self._coord
        if coord is None:
            return False
        return bool(coord.recording or coord.playing_id)

    async def _play_speech(self, path: Path) -> None:
        """Play a card name or an error tone. A missing speaker is logged.

        Waits out an in-progress recording or queued note, and stops early
        if one starts, so the record beep is not stuck behind a sentence.
        The name is played after that recording finishes.
        """
        if not path.is_file():
            log.warning("announcement file %s is missing", path)
            return
        proc = None
        try:
            while not self._stop.is_set():
                while not self._stop.is_set() and self._speech_blocked():
                    await asyncio.sleep(0.05)
                if self._stop.is_set():
                    return
                async with self._speaker_lock:
                    if self._speech_blocked():
                        continue
                    try:
                        proc = await self.audio.play(path)
                    except Exception:
                        log.exception("announcement playback failed")
                        return
                    interrupted = False
                    code: int | None = None
                    while True:
                        try:
                            code = await asyncio.wait_for(proc.wait(), timeout=0.05)
                            break
                        except TimeoutError:
                            if self._stop.is_set() or self._speech_blocked():
                                interrupted = True
                                if proc.returncode is None:
                                    proc.terminate()
                                try:
                                    await asyncio.wait_for(proc.wait(), timeout=1)
                                except TimeoutError:
                                    proc.kill()
                                break
                    proc = None
                    if interrupted:
                        if self._stop.is_set():
                            return
                        continue
                    if code not in (0, None):
                        log.warning("could not play %s (ffmpeg %s)", path.name, code)
                    return
        except asyncio.CancelledError:
            if proc is not None and proc.returncode is None:
                proc.terminate()
            raise
        except Exception:
            log.exception("announcement playback failed")

    async def _shutdown_audio(self) -> None:
        self._interrupt_playback()
        proc = self._capture_proc
        path = self._capture_path
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except (TimeoutError, ProcessLookupError):
                proc.kill()
        if path is not None:
            path.unlink(missing_ok=True)
        self._capturing = False


def _job(func, send: bool, duration: float):
    async def run():
        await func(send, duration)

    return run


def _failure_delay(exc: BaseException, attempts: int) -> float:
    retry_after = getattr(exc, "retry_after", None)
    if retry_after:
        return float(retry_after) + 1
    return retry_delay(attempts)


def token_from_config(config: AppConfig) -> str:
    """Environment first, then the root env file, then a local .env for laptops."""
    if config.dev_mode:
        return load_token(Path(".env"), config.paths.env_file)
    return load_token(config.paths.env_file, Path(".env"))
