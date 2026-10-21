# Roadmap

Recordings go to the family chat until a card is tapped. After that they go to the chat on that card, and they stay there until a different card is tapped, including across a reboot. Playing a queued note is unchanged: notes never start by themselves.

## NFC cards

Shipped:

- PN532 V3 on I2C bus 1, address 0x24 (decimal 36 in the config). NTAG215 cards.
- Wiring: VCC physical pin 1 (3.3V), GND pin 6, SDA pin 3, SCL pin 5. The button stays on BCM 17 (physical pin 11). BCM 26 (physical pin 37) stays free for the lamp.
- One `[[cards]]` entry per card: `uid`, `chat_id`, `announce`, optional `announce_file`. A card chat must be in `allowed_chat_ids`. More cards are more tables. There is no limit of two.
- `python -m messagebox --scan-card` prints a UID. The service must be stopped first.
- Every new presentation speaks the announce text with espeak-ng, or plays `announce_file` when that wav exists. A missing speaker or a missing espeak-ng is logged. The service keeps running.
- Unknown UIDs are logged and, when a speaker exists, get the error tone. The selected chat does not change.
- The last UID is stored in `/var/lib/message-box/selection.json` and restored on boot without speaking. The first boot uses `family_chat_id`.
- The reader is polled on a thread named `messagebox-nfc`. A missing reader leaves the box as a single chat. `--dev` does not open I2C.
- `recipient.idle_timeout_seconds` is still accepted and ignored. The service does not call `revert_if_idle`.

Not built, on purpose:

- Registering a card from the button, with no editor. The config file is the list.
- Recording the spoken name on the box. Put a wav path in `announce_file`, or let espeak-ng say `announce`.

`target_chat_id` is stored when a recording starts. A card tapped while that recording is open does not retarget it. The next recording uses the new card.

The button code does not read cards. The card thread only queues a UID.

## Still out of scope

- Messages never start playing on their own.
- One button only.
- No WhatsApp, and no dependency on the Button Box installer. This project only talks to Telegram.
- Config is read at startup. Restart the service after editing it.
- If the power fails after Telegram has accepted a voice note and before the outbox file is deleted, that note can be sent again on the next boot. A second copy in the chat is preferable to losing it.
