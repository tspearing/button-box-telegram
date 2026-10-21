# Message Box

A screen-free voice-note box for a young child. There is one big button.

- **Hold** the button until you hear a beep, talk, and let go. The box sends a Telegram voice note to the selected chat.
- **Tap** the button to hear the oldest note waiting in the queue. It is deleted after it plays.
- **Tap an NFC card** to choose which chat the next recording goes to. The speaker says that chat's name. The choice stays until a different card is tapped.
- Notes never play by themselves, and a new note never interrupts a recording.
- A lamp (optional) stays on while something is waiting, blinks quickly while recording, and is off when the queue is empty.

It runs on a Raspberry Pi 5 with 64-bit Raspberry Pi OS (Bookworm, Lite is ideal) and a Telegram bot you create yourself. The idea is in the same family as [button.box](https://button.box/diy). This is separate software: it does not use WhatsApp, and it does not use the Button Box installer.

Without a reader, or before any card is tapped, recordings go to the family chat. Each extra chat gets its own NTAG215 card. Tapping a card and pressing the button work at the same time: the reader is polled on its own thread and does not block recording or Telegram. See [docs/ROADMAP.md](docs/ROADMAP.md) for what is still not built.

## What you need

| Part | Notes |
| --- | --- |
| Raspberry Pi 5, 8GB | The software uses gpiozero with lgpio, which works on the Pi 5. Do not use RPi.GPIO. |
| Official Pi 5 power supply | 5V / 5A USB-C. A weak supply plus a USB speaker will reboot the Pi. |
| microSD card, 16GB or larger | 64-bit Raspberry Pi OS Bookworm Lite. |
| TONOR G11 USB microphone | It shows up as a USB sound card. No extra driver. |
| LIELONGREN USB speaker | It must be a USB audio speaker, not Bluetooth-only. |
| 100mm arcade button | With a microswitch, and a 12V lamp if you want the light. |
| Two wires for the switch | The switch connects to the Pi directly. No resistor. |
| Lamp parts, optional | A small NPN transistor (2N2222), a 1k resistor, a 100k resistor, and a 12V supply. See below. |
| HiLetgo PN532 NFC module V3 | I2C mode, 3.3V. Address 0x24 on bus 1. Optional: with no reader the box stays a single chat. |
| NTAG215 cards | One card per chat. Two are enough to start. More chats are more cards later. |

You can build and test all of this on the bench, with no box around it.

## How a day of use works

1. Someone in the family chat sends a voice note (or an audio file) to the bot.
2. The Pi downloads it and stores it on the SD card. The lamp comes on. Nothing plays yet.
3. The child taps the button. The oldest note plays. It is then deleted. Tap again for the next one.
4. The child holds the button. After about 0.6 seconds there is a short beep. That means "talk now".
5. They let go. The recording is turned into an OGG/Opus file and sent to the selected chat. A second, higher beep means Telegram accepted it.
6. If they let go less than about a second after the beep, the recording is thrown away and a low tone plays instead. The longest recording is 2 minutes; it sends itself at 2 minutes even if the button is still held.
7. A tap when nothing is waiting does nothing. You can turn on a soft "nothing there" tone in the config (`play_empty_tone = true`).

If the Wi-Fi drops, recordings wait on disk and are sent when the network comes back. Notes that arrived while the Pi was offline are picked up when Telegram reconnects. Nothing is played during a recording, including a note that arrives halfway through. A card announcement waits until the recording (and any queued note that is playing) has finished. If a recording starts while the name is playing, the name stops so the record beep can play, then the name plays again afterwards.

A card left on the reader does not repeat its name. Lift it and tap again to hear it, even when that chat is already selected. The last card is remembered in `/var/lib/message-box/selection.json` and restored on boot without speaking. The first boot, with nothing saved, uses `family_chat_id`.

## Wiring

BCM numbers go in the config file. Physical pin numbers are what you count on the header. They are not the same.

**Physical pin 17 is 3.3V power. Do not connect the button to it.** The button uses **BCM 17**, which is **physical pin 11**.

Pin 1 is marked on the Pi 5 board (it is a 3.3V pin). Count from that pin. One row is the odd pins (1, 3, 5, 7, 9, 11, …). The other row is the even pins (2, 4, 6, 8, 10, …).

| What | BCM (in `config.toml`) | Physical pin |
| --- | --- | --- |
| Button switch, one side | 17 | **11** |
| Button switch, other side | ground | **9** (GND) |
| Lamp driver, optional | 26 | **37** |
| Lamp driver ground | ground | **39** (or any GND) |
| PN532 VCC (3.3V only) | — | **1** |
| PN532 SDA | 2 (I2C, not a config pin) | **3** |
| PN532 SCL | 3 (I2C, not a config pin) | **5** |
| PN532 GND | ground | **6** |

```
                 odd pins          even pins
                 (pin 1 row)
        3.3V      1  o  o  2       5V        <-- PN532 VCC. Not pin 2.
        SDA       3  o  o  4                 <-- PN532 SDA
        SCL       5  o  o  6       GND       <-- PN532 SCL, and PN532 GND on pin 6
                  7  o  o  8
        GND       9  o  o  10
        GPIO17   11  o  o  12                <-- button switch between pin 11 and pin 9
        ...
        GPIO26   37  o  o  38                <-- lamp transistor, optional
        GND      39  o  o  40
```

### The switch

The arcade button's microswitch has three tabs: COM (common), NO (normally open), and NC (normally closed). Use **COM and NO**.

- One wire: COM to physical pin 11.
- Other wire: NO to physical pin 9.

The Pi turns on its internal pull-up. You do not add a resistor. When the button is pressed, the pin is connected to ground. Do not connect the switch to 5V or 12V.

If a recording starts the moment the service boots, you have used the NC tab. Move the wire to NO.

### The lamp

The lamp in a 100mm button is a **12V LED**. A Pi pin is 3.3V and can only supply a few mA. Connecting the lamp to the Pi will damage the Pi. The software still runs with the lamp wires not connected to anything; the pin simply switches on and off into empty air.

When you do want the lamp, use the Pi pin to switch a transistor, and power the lamp from 12V. A 2N2222 (a common small NPN transistor) is enough for a normal arcade lamp. The 12V supply's negative must be connected to the Pi's ground.

```
  +12V ------ lamp + 
               lamp -
                 |
                 +------ collector of 2N2222
                 |
  physical pin 37 (GPIO26) ----[ 1k ]---- base of 2N2222
                 |
                 +----[ 100k ]----+
                 |                |
                 +-- emitter -----+---- Pi GND (physical pin 39)
                                  |
                                  +---- negative of the 12V supply
```

- The **1k** resistor stops the Pi from putting too much current into the transistor base.
- The **100k** resistor holds the lamp off while the Pi is booting, when the pin is not being driven yet.
- A logic-level MOSFET (for example an IRLZ44N or AO3400) works too: pin 37 to the gate through 1k, 100k from gate to source, source to ground, lamp between +12V and the drain. Do not use a 2N7000 unless you know it turns fully on at 3.3V; many of them do not.

Set `lamp.enabled = false` in the config if you want the pin to stay off. Default is on, pin BCM 26.

While messages are waiting the lamp stays lit (`waiting = "steady"`). Change that to `"blink"` for a short flash every couple of seconds. During recording it blinks quickly either way.

### The NFC reader

Power the PN532 from **3.3V (physical pin 1)** only. Do not use physical pin 2 (5V). The module must be in I2C mode. On a board that is already working, `i2cdetect -y 1` prints `24` at address 0x24. SDA is physical pin 3, SCL is physical pin 5, and ground is physical pin 6.

`install.sh` turns on I2C (`dtparam=i2c_arm=on`), sets `dtparam=i2c_arm_baudrate=10000`, installs `i2c-tools` and `espeak-ng`, and puts the `messagebox` user in the `i2c` group. The systemd unit also lists that group. The slower clock is for the PN532: it holds the I2C clock line while it wakes, and a Pi 5 reports the resulting NAK as `Remote I/O error` (errno 121). `i2cdetect` can still show `24` when that happens. The baudrate line applies only after a reboot. `install.sh` says whether a reboot is required. A missing reader does not stop the service: the log says the reader is not available, and recordings keep going to the default chat.

BCM 26 (physical pin 37) stays free for the lamp. The button stays on BCM 17 (physical pin 11). The reader does not use either pin.

## 1. Flash the SD card

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer.
2. Choose device **Raspberry Pi 5**.
3. Choose OS **Raspberry Pi OS (other)** → **Raspberry Pi OS Lite (64-bit)**. That is Bookworm. The full desktop image also works; Lite is what this was written for.
4. Open the imager's settings (the gear, or "edit settings") before you write:
   - hostname: `message-box`
   - enable SSH (password is fine)
   - set a username and password you will remember
   - set the Wi-Fi name and password
5. Write the card, put it in the Pi, plug in power, and wait a minute.
6. From your computer: `ssh YOUR_USER@message-box.local` (or the IP address from your router).

Then, on the Pi:

```bash
sudo apt update
sudo apt full-upgrade
sudo apt install -y git
git clone https://github.com/tspearing/button-box-telegram.git
cd button-box-telegram
```

## 2. Create the bot

Do this on your phone, in Telegram.

1. Open a chat with **@BotFather**.
2. Send `/newbot`.
3. Give it a display name, for example `Message Box`.
4. Give it a username ending in `bot`, for example `message_box_bot`. BotFather replies with a token: a number, a colon, and a long string.
5. Send `/setprivacy`, choose the bot, and choose **Disable**. This matters. In a group, a bot with privacy mode left on does not see normal voice notes, so the box would never hear the family.
6. Send `/setjoingroups` and make sure groups are allowed.

Keep the token private. It is the password for the bot. It goes in `/etc/message-box/env` on the Pi, which is readable only by root. It does not go in the config file, and it must never be committed to this repository.

## 3. Install on the Pi

Plug in the USB microphone and the USB speaker first, so you can test them in a moment.

```bash
cd button-box-telegram
sudo bash install.sh
```

The script installs `ffmpeg`, `alsa-utils`, `python3-venv`, `python3-lgpio`, `espeak-ng`, and `i2c-tools`, creates a virtualenv at `/opt/message-box/venv`, a user called `messagebox`, and a systemd service that starts on boot. It enables I2C and adds that user to the `i2c` group. It is safe to run again after `git pull`. It will not overwrite an existing token file or config.

It writes:

| Path | What it is |
| --- | --- |
| `/etc/message-box/env` | The bot token and `LG_WD`. Root only, mode 600. |
| `/etc/message-box/config.toml` | Pins, audio, and the chat allowlist. No secrets. |
| `/var/lib/message-box` | Queued audio. Mode 700, owned by `messagebox`. |
| `/etc/systemd/system/message-box.service` | Starts the box on boot and restarts it if it crashes. |

Edit the token. One line, no quotes, no spaces around the `=`:

```bash
sudo nano /etc/message-box/env
```

```
TELEGRAM_BOT_TOKEN=123456789:paste-the-token-from-BotFather
LG_WD=/var/lib/message-box
```

`LG_WD` is where lgpio creates its notify pipe. The installer adds that line, and the service also sets it. Leave it pointed at `/var/lib/message-box`. The `messagebox` user cannot write inside `/opt/message-box`, and lgpio fails immediately if the pipe file cannot be created.

The service was not started if the token was empty. You still need the family chat id before incoming notes will be accepted. You can start the service now so the bot can answer `/chatid`:

```bash
sudo systemctl restart message-box
```

## 4. Get the family chat id

1. In Telegram, make a group for the family. Add the bot to the group.
2. Send any message in the group, for example `hello`.
3. Send `/chatid` in that group.

The bot replies with a number. A group id is negative and often starts with `-100`. That number is the one you want.

On the Pi:

```bash
sudo nano /etc/message-box/config.toml
```

Set both lines. Replace `CHAT_ID` with the number `/chatid` printed. A group id is negative.

```toml
[telegram]
allowed_chat_ids = [CHAT_ID]
family_chat_id = CHAT_ID
```

`allowed_chat_ids` is the security boundary. Voice notes from any other chat are ignored, and the box will not send anywhere else. An empty list does **not** mean "allow everyone"; it means "accept nobody".

If the list has exactly one chat, you can leave `family_chat_id` out and that chat is used.

```bash
sudo systemctl restart message-box
```

### If you would rather not use `/chatid`

Stop the service so it is not polling at the same time, then ask the API what it has seen:

```bash
sudo systemctl stop message-box
sudo -u messagebox /opt/message-box/venv/bin/python -m messagebox chatid
```

Message the bot first (`/start` in a private chat, or any message in the group). The command prints each chat id and a config snippet. It does not consume those updates, so the service can still download a voice note that arrived in the meantime. Start the service again when you are done.

Only one program can poll a bot at a time. Leave the service running and use `/chatid`, or stop the service and use the command. Not both.

## 5. NFC cards, to choose a chat

Each chat gets one card. Tapping it selects that chat until a different card is tapped. There is no idle timeout and no automatic return to the family chat. The button still records and plays; it does not wait for the reader.

After `git pull` on a box that is already installed:

```bash
cd button-box-telegram
git pull
sudo bash install.sh
i2cdetect -y 1
```

`i2cdetect` should print `24`. Reboot if `install.sh` says a reboot is required (it does when it has just set the I2C clock to 10000 Hz). Skip the reboot when it says the clock is already 10000 Hz and `24` is already there.

Stop the service before scanning. The service and `--scan-card` cannot share the reader.

```bash
sudo systemctl stop message-box
sudo -u messagebox /opt/message-box/venv/bin/python -m messagebox --scan-card
```

Tap the first card. The command prints a UID and a `[[cards]]` snippet. Run it again and tap the second card. Then edit `/etc/message-box/config.toml`. Use the chat ids already in `allowed_chat_ids` (the same numbers `/chatid` printed). The numbers below are placeholders.

```toml
[telegram]
allowed_chat_ids = [-1000000000001, -1000000000002]
family_chat_id = -1000000000001

[[cards]]
uid = "PASTE_UID_FROM_SCAN"
chat_id = -1000000000001
announce = "German"

[[cards]]
uid = "PASTE_UID_FROM_SCAN"
chat_id = -1000000000002
announce = "English"
```

`announce` is the offline phrase espeak-ng says on every tap, including a tap of the card that is already selected. A wav file replaces speech when the file is present:

```toml
announce_file = "/var/lib/message-box/clips/german.wav"
```

If that file is missing, the box logs it and speaks `announce` instead. `chat_id` must be in `allowed_chat_ids`. Any other id is rejected at startup (`Config error` in the journal) and the service does not start.

```bash
sudo systemctl restart message-box
journalctl -u message-box -f
```

Tap a card. The log shows the UID and the chat, and the speaker says the name when a speaker is plugged in. Hold the button and confirm the voice note arrives in that chat. Tap the other card and record again. A card that is not in the config logs `unknown card` plus the UID, and plays a short error tone when a speaker exists. It does not change the selected chat.

The USB speaker is not required for switching. With no playback device the service logs that the announcement could not be played and keeps running. The chat still changes.

After a reboot, the log line `restored card` means the last choice was loaded and no new tap is required. A recording then goes to that chat.

`--scan-card` does not need a bot token. An unknown card in the journal is another way to read a UID while the service is up, if you would rather not stop it to scan.

Add more chats later with more `[[cards]]` tables and more ids in `allowed_chat_ids`. Restart after editing the config.

## 6. Bench test, no enclosure

Do this before you build a case.

### Microphone and speaker

```bash
sudo -u messagebox /opt/message-box/venv/bin/python -m messagebox --check-audio
```

Run that from any directory. It reads `/etc/message-box/config.toml`. A `config.toml` in the current directory is used only when the `messagebox` user can open it. An unreadable one is skipped. That is normal when the command is started from a home directory, and it is not an error when the system config can be read.

You should hear a beep, then two seconds of recording, then that recording played back. The command prints which ALSA devices it chose. The beep and the test recording are written under the data directory (`/var/lib/message-box`, or a temp directory if that folder is not writable), not in the folder you started the command from.

If no USB microphone or speaker is found, the command says so and does not claim a clean pass. Playback through ALSA's default device can succeed and still be silent.

If it picks the wrong device, run `arecord -l` and `aplay -l` and set the devices in `/etc/message-box/config.toml`:

```toml
[audio]
mic_device = "plughw:1,0"
speaker_device = "plughw:2,0"
```

`plughw` lets ALSA convert sample rates, which is what you want. You can also override for one command without editing the file:

```bash
sudo -u messagebox MESSAGEBOX_MIC=plughw:1,0 MESSAGEBOX_SPEAKER=plughw:2,0 \
  /opt/message-box/venv/bin/python -m messagebox --check-audio
```

### The button and a real note

Watch the log, then hold the button for a couple of seconds after the beep and let go:

```bash
journalctl -u message-box -f
```

The selected chat should get a voice note, and you should hear the "sent" beep. Then reply with a voice note from your phone. The log should say it was queued. Tap the button (a press shorter than half a second). The note plays, and a second tap does nothing if the queue is empty. A card tap during this test does not block the button.

Leave the lamp disconnected for this test. The log will still say `lamp waiting` or `lamp recording` if GPIO is working. If opening the lamp pin fails, the box keeps running without it.

### Useful commands

```bash
systemctl status message-box
sudo systemctl restart message-box
journalctl -u message-box -n 100 --no-pager
```

Logs go to the journal because the service's stdout and stderr are what systemd records. There is no separate log file.

After a `git pull` on the Pi, run `sudo bash install.sh` again. It copies the new code into `/opt/message-box` and restarts the service if a token is set.

## Dev mode on a laptop

You can run the same program without GPIO. The keyboard stands in for the button. Incoming and outgoing notes behave the same, as long as the laptop has a microphone, a speaker, and `ffmpeg`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# put TELEGRAM_BOT_TOKEN=... in .env
cp config.example.toml config.toml
# set allowed_chat_ids and family_chat_id in config.toml
python -m messagebox --dev
```

`.env` and `config.toml` are gitignored. Do not commit them.

In the terminal:

| Key | What it does |
| --- | --- |
| Hold SPACE | Record. Let go to stop. A quick tap of SPACE plays the next note. |
| `s` | Short press, if holding SPACE is awkward. |
| `r` | Press once to start a hold, press again to let go. |
| `q` | Quit. |

SPACE needs key repeat. A lone tap (no repeat within about 0.7 seconds) is a short press. If your keyboard repeats slowly and a tap starts a recording, raise `dev.repeat_window_seconds` in the config so it is longer than the repeat delay. `s` and `r` do not depend on repeat.

On a laptop the queue is stored in `./data` when `/var/lib/message-box` is not writable. On the Pi, `--dev` still uses the data directory from the config if that directory is writable, which is useful for trying the keyboard on a Pi that does not have the button wired yet:

```bash
sudo systemctl stop message-box
sudo -u messagebox /opt/message-box/venv/bin/python -m messagebox --dev
```

`--check-audio` does not need a token. `python -m messagebox chatid` does. `--scan-card` does not need a token, but it does need the PN532, so it is a Pi command. `--dev` does not open the reader.

### Tests

```bash
pip install -e ".[dev]"
pytest
```

The tests cover the queue (order, and that it is still there after a new process opens the same folder), the button state machine (tap versus hold, too-short recordings, the two-minute cap), the rule that an incoming note does not start playback, card selection, the PN532 frame parser, and announcements with a stand-in for espeak-ng. They do not need a Pi, a reader, or a Telegram token.

## Config reference

`config.example.toml` is commented. The settings you are most likely to change:

| Setting | Default | Meaning |
| --- | --- | --- |
| `button.hold_seconds` | `0.6` | How long a press must last before recording starts. |
| `button.min_record_seconds` | `1.0` | Audio shorter than this, after the beep, is discarded. |
| `button.max_record_seconds` | `120` | Recording stops and sends at this length. |
| `lamp.enabled` | `true` | Set `false` to force the lamp pin off. |
| `lamp.waiting` | `steady` | `steady` or `blink` while notes are queued. |
| `audio.mic_device` / `speaker_device` | `auto` | Or an ALSA device such as `plughw:2,0`. |
| `audio.play_empty_tone` | `false` | Soft tone when a tap finds an empty queue. |
| `telegram.allowed_chat_ids` | empty | Chats that may send, and the only chats we will send to. |
| `nfc.i2c_bus` / `i2c_address` | `1` / `36` | PN532 bus and address. 36 is 0x24. |
| `[[cards]]` | none | `uid`, `chat_id`, `announce`, optional `announce_file`. |
| `recipient.idle_timeout_seconds` | `120` | Ignored. The selected card does not time out. |

Sounds (the beep, the sent chime, the too-short tone) are generated by the program as tiny WAV files. They are not recorded from anywhere else.

## Troubleshooting

**The bot ignores voice notes in the group.** Privacy mode is still on. In BotFather, `/setprivacy` → Disable, then remove the bot from the group and add it again (Telegram only applies the change on re-add).

**`/chatid` says the bot is already polling, or the chatid command reports a conflict.** Stop the service first, or just send `/chatid` while the service is running. Do not do both.

**No sound, or the wrong device.** `arecord -l` and `aplay -l`. Set `mic_device` and `speaker_device`. The hints `TONOR` and `LIELONGREN` are only used for auto-detection. HDMI and the Pi headphone jack are skipped on purpose. `--check-audio` warns when it had to use ALSA's default device, which is often silent.

**Recording fails immediately.** Check the mic is plugged in. `journalctl -u message-box -n 50` will say if ffmpeg could not open the device. The `messagebox` user must be in the `audio` group; `install.sh` does that. If you created the user by hand, `sudo usermod -a -G audio,gpio,i2c messagebox` and restart the service.

**The button does nothing.** The switch wires go between physical pin 11 and physical pin 9, using COM and NO. In the log you want `button press` and `button release`. `GPIO button failed` means lgpio cannot open the chip: install `python3-lgpio`, and the service user needs the `gpio` group. The pin factory is lgpio on purpose, because RPi.GPIO does not work on the Pi 5.

**The lamp is on all the time, even with no messages.** The transistor gate is floating. Add the 100k resistor from the base (or gate) to ground. Or set `lamp.enabled = false`.

**The lamp never lights.** 12V supply negative must be tied to Pi ground. The lamp is between +12V and the transistor collector, not between the GPIO and ground.

**A note was not delivered.** It is still in `/var/lib/message-box/outbox` and will be retried, with the wait doubling up to five minutes. Sending only happens to a chat on the allowlist.

**The service crashes with `xCreatePipe` or `.lgd-nfy`.** lgpio creates a pipe file in its working directory. `/opt/message-box` is not writable by the `messagebox` user, so the unit sets `WorkingDirectory` and `LG_WD` to `/var/lib/message-box`. Re-run `sudo bash install.sh` after updating. Do not point `LG_WD` at `/opt/message-box`.

**`Config error: Permission denied: 'config.toml'`.** The process found `config.toml` in the directory you launched from, and the `messagebox` user cannot read it. That file is skipped. When `/etc/message-box/config.toml` can be read, this is silent. Put the real settings in that system file. `--check-audio`, `--dev`, `--scan-card`, and `chatid` can be started from any directory.

**The service will not stay up.** `systemctl status message-box` and the journal. A missing token, a `family_chat_id` that is not in `allowed_chat_ids`, or a card whose `chat_id` is not on that list exits on purpose. An empty allowlist does not exit: the bot stays up so `/chatid` works, and recordings are kept until you set the chat and restart. `Failed to resolve group i2c` means the unit was installed before the `i2c` group existed. Run `sudo bash install.sh` again.

**No `24` in `i2cdetect -y 1`.** The reader is unplugged, still in UART mode, or I2C is off. Power is physical pin 1 (3.3V), ground pin 6, SDA pin 3, SCL pin 5. Re-run `sudo bash install.sh` and reboot if it added `dtparam=i2c_arm=on`. The service still runs without the reader.

**`--scan-card` says errno 121 or `Remote I/O error`, but `i2cdetect` shows 24.** The chip answered its address and then NAK'd a real transfer. A Pi 5 reports that NAK as errno 121. Re-run `sudo bash install.sh`. If it says a reboot is required, `sudo reboot`, stop the service, and scan again. The program already retries a wake-up NAK. After the reboot the I2C clock should be 10000 Hz. The scan error prints the live clock.

**A card tap does nothing.** The UID in the log is `unknown card` until that UID is a `[[cards]]` entry. `--scan-card` while the service is running fails or mis-reads because both want the PN532. Stop the service, scan, edit the config, start the service.

**The name is not spoken.** espeak-ng comes from `install.sh`. The log says when it is missing, when the wav override is missing, or when playback failed because no speaker is plugged in. None of those stop the service, and the chat still changes. A card that is already selected speaks again only after you lift it and tap again.

**`--scan-card` says the reader is in use.** `sudo systemctl stop message-box`, then scan. Start the service when both cards are in the config.

**ProtectSystem errors.** The service is locked down so it can only write under `/var/lib/message-box`. If a future audio library needs another path, that is the `ProtectSystem` and `ReadWritePaths` lines in `deploy/message-box.service`.

## Privacy

The bot token is the keys to the bot. `/etc/message-box/env` is root-only. The SD card holds family voice notes under `/var/lib/message-box` until they are played or sent. Treat the card as private.

The allowlist is the whole of the access control in v1. Do not add the bot to a public group. Logs record chat ids and whether a send worked. They are filtered so a token that shows up inside a library error is redacted, but do not paste journal output into a public place without looking at it.

## Layout

| Path | Role |
| --- | --- |
| `messagebox/button_machine.py` | Tap versus hold. No GPIO. |
| `messagebox/coordinator.py` | What a tap, a hold, and an incoming note do. |
| `messagebox/queue_store.py` | On-disk inbox and outbox. |
| `messagebox/recipients.py` | Who we are sending to. The family chat, or the last card. |
| `messagebox/inputs/` | GPIO button, keyboard, and the PN532 reader. |
| `messagebox/announce.py` | espeak-ng, or a wav file, for the card name. |
| `messagebox/runtime.py` | Wires audio, the lamp, cards, and Telegram together. |
| `install.sh`, `deploy/message-box.service` | Pi install and boot. |

`python -m messagebox` is the program. `python -m messagebox --help` lists `--dev`, `--check-audio`, `--scan-card`, and `chatid`. Telegram is [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) version 21 (the `21.x` line, not 22).
