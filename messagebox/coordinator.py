"""Glue between the button machine and the inbox.

This is the behaviour of the box, without GPIO, audio devices, or Telegram:

* a short press plays the oldest queued note, then the runtime removes it
  after playback succeeds
* a hold records, and incoming notes that arrive during the recording stay
  queued and never start playing
* the lamp is on (or blinking) while notes are waiting, off when the queue
  is empty, and uses a distinct mode while recording
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from messagebox.button_machine import Action, ActionKind, ButtonMachine

LAMP_OFF = "off"
LAMP_WAITING = "waiting"
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


class InboxView(Protocol):
    def __len__(self) -> int: ...

    def peek(self) -> object | None: ...

    def remove(self, item_id: str) -> None: ...


class Coordinator:
    def __init__(
        self,
        machine: ButtonMachine,
        inbox: InboxView,
        *,
        play_empty_tone: bool = False,
    ) -> None:
        self.machine = machine
        self.inbox = inbox
        self.play_empty_tone = play_empty_tone
        self.recording = False
        self.playing_id: str | None = None
        self._lamp: str | None = None

    def lamp_mode(self) -> str:
        if self.recording:
            return LAMP_RECORDING
        if len(self.inbox) > 0 or self.playing_id is not None:
            return LAMP_WAITING
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

    def playback_finished(self, item_id: str, *, success: bool) -> list[Effect]:
        """Drop the note after it has been heard. Keep it if playback failed."""
        if success:
            self.inbox.remove(item_id)
        if self.playing_id == item_id:
            self.playing_id = None
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
            return [Effect(EffectKind.STOP_RECORD, recorded_for=action.recorded_for)]
        if action.kind is ActionKind.RECORD_DISCARD:
            self.recording = False
            return [Effect(EffectKind.DISCARD_RECORD, recorded_for=action.recorded_for)]
        if action.kind is ActionKind.RECORD_MAX:
            # Keep the recording lamp until they let go, but stop the mic now
            # so the two-minute note can be sent while the button is still down.
            self.recording = True
            return [Effect(EffectKind.STOP_RECORD, recorded_for=action.recorded_for)]
        if action.kind is ActionKind.HOLD_CLEARED:
            self.recording = False
            return []
        return []

    def _short_press(self) -> list[Effect]:
        if self.recording or self.playing_id is not None:
            return []
        item = self.inbox.peek()
        if item is None:
            if self.play_empty_tone:
                return [Effect(EffectKind.EMPTY)]
            return []
        item_id = getattr(item, "id")
        self.playing_id = str(item_id)
        return [Effect(EffectKind.PLAY, item_id=self.playing_id)]

    def _with_lamp(self, effects: list[Effect]) -> list[Effect]:
        mode = self.lamp_mode()
        if mode != self._lamp:
            self._lamp = mode
            effects.append(Effect(EffectKind.LAMP, lamp=mode))
        return effects
