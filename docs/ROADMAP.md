# Roadmap

Version 1 sends every recording to one family chat and plays whatever that chat sends back. The behaviour below is planned, not built. The hooks it needs are already in the code so it can be added without replacing the button, the queue, or Telegram.

## NFC cards, to choose a person

The child taps a card or a fob on the box. The speaker says that person's name, or plays a short clip recorded in their voice. Until the box has been idle for a while, the big button sends to that person instead of the family chat. After the idle timeout it goes back to the family chat. A spare card can be registered again to someone else.

### Hardware

- Reader: PN532 V3, on the Pi's I2C bus. Not USB.
- Tags: NTAG215 cards and fobs.
- Wiring, 3.3V only (the PN532 must be strapped for 3.3V, not 5V):
  - SDA to physical pin 3 (BCM 2)
  - SCL to physical pin 5 (BCM 3)
  - 3.3V and GND to the usual header pins
- Enable I2C on Raspberry Pi OS (`dtparam=i2c_arm=on`). The Pi 5 exposes the same I2C pins as earlier boards.

The reader must not be polled in a way that stalls the button. A tap is just another input event.

### Behaviour to implement

1. A tap of a known card selects that recipient.
2. The speaker plays `Recipient.announce_clip` if one has been stored, otherwise a short tone. It must not do this in the middle of a recording.
3. Recordings started while that person is selected are addressed to their chat id.
4. Playing a queued note, or starting a recording, counts as activity and resets the idle timer.
5. When `idle_timeout_seconds` passes with no activity, the current recipient becomes the family chat again. The default in config is 120 seconds. Change it there when the feature is turned on.
6. An unknown card does nothing to the current recipient (a soft error tone is fine).
7. Registration of a spare card replaces whatever person that uid pointed at before. Suggested flow, still to design in detail:
   - an adult runs a command such as `python -m messagebox register-card person`, or uses a setup gesture we have not defined
   - the lamp shows a third pattern so it is obvious the box is waiting
   - they tap the card
   - the uid is written to disk (a `cards.json` under the data directory does not exist yet; registrations are in memory only today)
   - they can record a one-second name clip, stored beside the registration
8. A card cannot point at a chat that is not on `allowed_chat_ids`. The allowlist stays the security boundary.

### Where to plug it in

| Piece | What v1 already does |
| --- | --- |
| `messagebox/recipients.py` | `Recipient` and `RecipientDirectory`. `current` is who a new recording is sent to. `select_card`, `register_card`, `note_activity`, `revert_if_idle`, and `revert_to_default` are the card operations. Uids are compared with separators and letter case ignored. |
| `messagebox/runtime.py` | Calls `note_activity` when a note is stored or played, and `revert_if_idle` about once a second. With only the family selected, that changes nothing. |
| `messagebox/inputs/base.py` | `CardSource` protocol: `start(on_card)` and `stop()`. There is no PN532 driver. A new `messagebox/inputs/pn532.py` should only report uids, then the runtime calls `directory.select_card(uid, time.monotonic())`. |
| Outbox | `target_chat_id` is stored when the recording finishes. Changing recipient later does not retarget a note already queued. |

Do not read cards from inside the GPIO button code. The button source and the card source are separate inputs.

`Recipient.announce_clip` is the path for the name clip. Nothing plays it in v1.

## Not in v1, and not part of the card work either

- Messages never start playing on their own. Keep it that way unless there is a deliberate new gesture.
- One button only.
- No WhatsApp, and no dependency on the Button Box installer. This project only talks to Telegram.
- Config is read at startup. Restart the service after editing it.
- If the power fails after Telegram has accepted a voice note and before the outbox file is deleted, that note can be sent again on the next boot. A second copy in the family chat is preferable to losing it.
