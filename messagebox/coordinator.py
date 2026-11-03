"""Glue between the button machine and the inbox.

This is the behaviour of the box, without GPIO, audio devices, or Telegram:

* a short press plays the oldest unheard note for the focused chat
* when that chat has none waiting, the same press replays its latest note
* a note is kept after it is heard; older heard notes for that chat are dropped
* a hold records, and incoming notes that arrive during the recording stay
  queued and never start playing
* the lamp gives a short flash every 3 seconds while the focused chat has
  unheard notes, blinks evenly (1 second on, 1 second off) when only some
  other chat does, and is off when nothing is waiting
* the lamp goes steady only while the microphone is open, not when the
  button first goes down and not during the record beep
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from messagebox.button_machine import Action, ActionKind, ButtonMachine

LAMP_OFF = "off"
LAMP_WAITING = "waiting"
LAMP_ELSEWHERE = "elsewhere"
LAMP_RECORDING = "recording"


class EffectKind(Enum):
    PLAY = "play"
    EMPTY = "empty"
    START_RECORD = "start_record"
    STOP_RECORD = "stop_record"
    DISCARD_RECORD = "discard_record"
    LAMP = "lamp"


@dataclass(frozen=True)
class Effect:
    kind: EffectKind
    item_id: str | None = None
    lamp: str | None = None
    recorded_for: float = 0.0
    replay: bool = False


class _Playable(Protocol):
    id: str


class _Choice(Protocol):
    item: _Playable
    replay: bool


class InboxView(Protocol):
    def next_play(self, chat_id: int) -> _Choice | None: ...

    def mark_heard(self, item_id: str) -> None: ...

    def has_unheard(self, chat_id: int) -> bool: ...

    def has_unheard_elsewhere(self, chat_id: int) -> bool: ...


class Coordinator:
    def __init__(
        self,
        machine: ButtonMachine,
        inbox: InboxView,
        *,
        play_empty_tone: bool = False,
        focused_chat: Callable[[], int] | None = None,
    ) -> None:
        self.machine = machine
        self.inbox = inbox
        self.play_empty_tone = play_empty_tone
        self._focused_chat = focused_chat or (lambda: 0)
        self.recording = False
        self.mic_open = False
        self.playing_id: str | None = None
        self._replaying = False
        self._lamp: str | None = None

    def lamp_mode(self) -> str:
        # Steady only while the mic is open. Holding the button, and the
        # beep before the mic starts, leave the previous pattern in place.
        if self.mic_open:
            return LAMP_RECORDING
        chat_id = self._chat_id()
        if self.inbox.has_unheard(chat_id):
            return LAMP_WAITING
        if self.inbox.has_unheard_elsewhere(chat_id):
            return LAMP_ELSEWHERE
        return LAMP_OFF

    def boot(self) -> list[Effect]:
        """Lamp state after a restart, when the inbox may already have notes."""
        self._lamp = None
        return self._with_lamp([])

    def press(self, now: float) -> list[Effect]:
        return self._apply(self.machine.press(now))

    def release(self, now: float) -> list[Effect]:
        return self._apply(self.machine.release(now))

    def tick(self, now: float) -> list[Effect]:
        return self._apply(self.machine.tick(now))

    def message_enqueued(self) -> list[Effect]:
        """A note arrived. Queue only: never start playback from here."""
        return self._with_lamp([])

    def focus_changed(self) -> list[Effect]:
        """The focused chat changed. Do not start playback from here."""
        return self._with_lamp([])

    def mic_opened(self) -> list[Effect]:
        """The microphone is open. The lamp goes steady from this moment."""
        self.mic_open = True
        return self._with_lamp([])

    def mic_closed(self) -> list[Effect]:
        """The microphone has closed. The lamp shows whatever is still waiting."""
        self._release_mic()
        return self._with_lamp([])

    def _release_mic(self) -> None:
        """Drop the steady lamp. Safe to call if the mic was never open."""
        self.mic_open = False

    def playback_finished(self, item_id: str, *, success: bool) -> list[Effect]:
        """Keep a note once it has been heard. Keep it unheard if playback failed.

        A replay is already the retained note, so finishing it changes nothing
        on disk. A newer note for this chat is then what the next press plays.
        """
        current = self.playing_id == item_id
        replaying = current and self._replaying
        if current:
            self.playing_id = None
            self._replaying = False
        if success and current and not replaying:
            self.inbox.mark_heard(item_id)
        return self._with_lamp([])

    def _apply(self, actions: list[Action]) -> list[Effect]:
        effects: list[Effect] = []
        for action in actions:
            effects.extend(self._one(action))
        return self._coalesce(self._with_lamp(effects))

    def _coalesce(self, effects: list[Effect]) -> list[Effect]:
        """A release exactly at the hold threshold emits start+discard together.

        The mic was never really open, so only the discard cue is needed.
        """
        kinds = {effect.kind for effect in effects}
        if (
            EffectKind.START_RECORD in kinds
            and EffectKind.DISCARD_RECORD in kinds
            and EffectKind.STOP_RECORD not in kinds
        ):
            return [effect for effect in effects if effect.kind is not EffectKind.START_RECORD]
        return effects

    def _one(self, action: Action) -> list[Effect]:
        if action.kind is ActionKind.SHORT_PRESS:
            return self._short_press()
        if action.kind is ActionKind.RECORD_START:
            self.recording = True
            return [Effect(EffectKind.START_RECORD)]
        if action.kind is ActionKind.RECORD_STOP:
            self.recording = False
            self._release_mic()
            return [Effect(EffectKind.STOP_RECORD, recorded_for=action.recorded_for)]
        if action.kind is ActionKind.RECORD_DISCARD:
            self.recording = False
            self._release_mic()
            return [Effect(EffectKind.DISCARD_RECORD, recorded_for=action.recorded_for)]
        if action.kind is ActionKind.RECORD_MAX:
            # The note sends now, while the button is still down. The steady
            # lamp ends in this same step, not when they eventually let go.
            self.recording = True
            self._release_mic()
            return [Effect(EffectKind.STOP_RECORD, recorded_for=action.recorded_for)]
        if action.kind is ActionKind.HOLD_CLEARED:
            self.recording = False
            self._release_mic()
            return []
        return []

    def _chat_id(self) -> int:
        try:
            return int(self._focused_chat())
        except (TypeError, ValueError):
            return 0

    def _short_press(self) -> list[Effect]:
        if self.recording or self.playing_id is not None:
            return []
        choice = self.inbox.next_play(self._chat_id())
        if choice is None:
            # This chat has never had a note (or its copy is gone). The soft
            # empty cue is the existing nothing-tone, still behind the config
            # switch. Do not borrow a note from another chat.
            if self.play_empty_tone:
                return [Effect(EffectKind.EMPTY)]
            return []
        self.playing_id = str(choice.item.id)
        self._replaying = bool(choice.replay)
        return [
            Effect(EffectKind.PLAY, item_id=self.playing_id, replay=self._replaying)
        ]

    def _with_lamp(self, effects: list[Effect]) -> list[Effect]:
        mode = self.lamp_mode()
        if mode != self._lamp:
            self._lamp = mode
            effects.append(Effect(EffectKind.LAMP, lamp=mode))
        return effects
