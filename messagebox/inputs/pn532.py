"""PN532 NFC reader over I2C.

The HiLetgo PN532 V3, strapped for I2C, answers at 7-bit address 0x24
(shown as ``24`` by ``i2cdetect -y 1``). This speaks the PN532 frame
protocol on ``/dev/i2c-N`` directly. A missing reader is logged and
retried. It does not stop the button or Telegram.

Polling runs on its own thread. ``on_card`` is invoked only when a tag
is newly presented, so a card left sitting on the reader announces once.
Lift it and tap again, including a tap of the card that is already
selected, and it announces again.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from messagebox.recipients import normalize_uid

log = logging.getLogger("messagebox.nfc")
_unknown_targets: set[str] = set()

HOST_TO_PN532 = 0xD4
PN532_TO_HOST = 0xD5
ACK = bytes.fromhex("0000ff00ff00")
NACK = bytes.fromhex("0000ffff0000")
# libnfc's I2C wakeup: two 0x55 bytes, then a quiet preamble.
WAKE = b"\x55\x55" + b"\x00" * 14
GET_FIRMWARE = b"\x02"
# Normal mode, timeout 0x14, IRQ on. Matches the usual PN532 bring-up.
SAM_CONFIGURATION = bytes((0x14, 0x01, 0x14, 0x01))
# One poll, 150 ms period, generic 106 kbps ISO14443A (NTAG215).
IN_AUTO_POLL = bytes((0x60, 0x01, 0x01, 0x00))

_I2C_SLAVE = 0x0703
_I2C_TIMEOUT = 0x0702  # unit is 10 ms
# The PN532 holds SCL while it wakes (often hundreds of microseconds, up to
# about 2 ms). The Pi's I2C controller aborts that stretch at the default
# clock. 10 kHz is slow enough that the stretch fits inside a clock pulse.
# install.sh writes this into the boot config. It applies only after reboot.
I2C_BAUDRATE_HZ = 10000
# tBUF in the PN532 I2C timing table: wait after STOP before the next START.
_BUS_FREE_SECONDS = 0.005
_INIT_ATTEMPTS = 4
# Pi 5's I2C driver reports a NAK as EREMOTEIO (121). Older Pi drivers used
# EIO (5). A NAK while the chip is waking, or while it is not ready to be
# read, is normal and must be retried.
_RETRYABLE_ERRNOS = frozenset(
    number
    for number in (
        errno.EIO,
        errno.ETIMEDOUT,
        errno.EAGAIN,
        errno.ENXIO,
        getattr(errno, "EREMOTEIO", 121),
    )
    if number is not None
)


def encode_frame(tfi: int, data: bytes) -> bytes:
    """Build a normal information frame.

    ``LEN`` counts TFI plus data. ``LCS`` and ``DCS`` are the two's
    complement checksums from the PN532 manual.
    """
    body = bytes((tfi,)) + bytes(data)
    if len(body) > 255:
        raise ValueError("PN532 frame is too long")
    length = len(body)
    length_checksum = (0x100 - length) & 0xFF
    data_checksum = (0x100 - (sum(body) & 0xFF)) & 0xFF
    return b"\x00\x00\xff" + bytes((length, length_checksum)) + body + bytes((data_checksum, 0x00))


def build_frame(command: bytes) -> bytes:
    """A command frame from the host to the PN532."""
    return encode_frame(HOST_TO_PN532, command)


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and getattr(exc, "errno", None) in _RETRYABLE_ERRNOS


def read_i2c_clock_hz(bus: int) -> int | None:
    """The clock the running kernel is using, or None if sysfs has no value."""
    path = Path(f"/sys/class/i2c-adapter/i2c-{bus}/of_node/clock-frequency")
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) < 4:
        return None
    return int.from_bytes(raw[:4], "big")


def explain_i2c_failure(exc: BaseException, *, bus: int, address: int) -> str:
    """What to print when the chip ACKs its address but data transfers fail."""
    number = getattr(exc, "errno", None)
    parts = [f"PN532 at 0x{address:02x} on i2c-{bus} failed ({exc})."]
    if isinstance(exc, TimeoutError) or number in (121, errno.EIO, None):
        parts.append(
            "i2cdetect can still show 24. A Pi 5 reports a NAK from the PN532 "
            "as errno 121, and the chip also clock-stretches."
        )
        clock = read_i2c_clock_hz(bus)
        if clock is None:
            parts.append("The live I2C clock could not be read.")
        else:
            parts.append(f"The live I2C clock is {clock} Hz.")
        parts.append(
            "install.sh sets dtparam=i2c_arm_baudrate="
            f"{I2C_BAUDRATE_HZ} in the boot config. That speed is used only after a reboot."
        )
        if clock is not None and clock != I2C_BAUDRATE_HZ:
            parts.append("A reboot is required: sudo reboot")
        elif clock == I2C_BAUDRATE_HZ:
            parts.append(
                "The clock is already "
                f"{I2C_BAUDRATE_HZ} Hz. Check 3.3V on physical pin 1, "
                "SDA on pin 3, SCL on pin 5, and GND on pin 6."
            )
    return " ".join(parts)


def pop_frame(buf: bytes) -> tuple[str | None, bytes, bytes]:
    """Pull one frame off the front of ``buf``.

    Returns ``(kind, body, rest)``. ``kind`` is ``"ack"``, ``"nack"``,
    ``"data"``, or None when a whole frame has not arrived yet. ``body``
    includes the TFI byte.
    """
    while True:
        start = buf.find(b"\x00\x00\xff")
        if start < 0:
            if len(buf) > 2:
                buf = buf[-2:]
            return None, b"", buf
        buf = buf[start:]
        if len(buf) < 6:
            return None, b"", buf
        length = buf[3]
        length_checksum = buf[4]
        if length == 0x00 and length_checksum == 0xFF:
            return "ack", b"", buf[6:]
        if length == 0xFF and length_checksum == 0x00:
            return "nack", b"", buf[6:]
        if ((length + length_checksum) & 0xFF) != 0:
            buf = buf[1:]
            continue
        total = 7 + length
        if len(buf) < total:
            return None, b"", buf
        body = buf[5 : 5 + length]
        data_checksum = buf[5 + length]
        if ((sum(body) + data_checksum) & 0xFF) != 0:
            buf = buf[1:]
            continue
        return "data", body, buf[total:]


def uid_from_autopoll(payload: bytes) -> str | None:
    """Return the hex UID from an InAutoPoll payload, or None if no tag.

    ``payload`` is the response after the TFI byte: ``0x61``, target
    count, then per target a type, a length, and the target bytes.
    Type A target bytes are ATQA (2), SAK (1), UID length (1), UID.
    """
    if not payload:
        raise OSError("empty PN532 response")
    if payload[0] == 0x7F:
        raise OSError(f"PN532 error {payload.hex()}")
    if payload[0] != 0x61:
        raise OSError(f"unexpected PN532 response {payload.hex()}")
    if len(payload) < 2 or payload[1] == 0:
        return None
    offset = 2
    if offset + 2 > len(payload):
        return None
    offset += 1  # target type
    length = payload[offset]
    offset += 1
    target = payload[offset : offset + length]
    if len(target) != length:
        _log_unknown_target(payload)
        return None
    uid = _uid_from_type_a(target)
    if uid is None:
        _log_unknown_target(payload)
    return uid


def _log_unknown_target(payload: bytes) -> None:
    text = payload.hex()
    if text in _unknown_targets:
        return
    _unknown_targets.add(text)
    log.info("NFC target was not a card UID we understand: %s", text)


def _uid_from_type_a(target: bytes) -> str | None:
    if len(target) < 4:
        return None
    uid_len = target[3]
    if not 4 <= uid_len <= 10:
        return None
    uid = target[4 : 4 + uid_len]
    if len(uid) != uid_len:
        return None
    return uid.hex().upper()


class CardEdgeDetector:
    """Fire once per presentation.

    A card that stays on the reader does not fire again. ``miss_limit``
    empty polls are required before the next presentation counts, so one
    glitch poll does not announce twice.
    """

    def __init__(self, miss_limit: int = 3) -> None:
        if miss_limit < 1:
            raise ValueError("miss_limit must be at least 1")
        self.miss_limit = miss_limit
        self._present: str | None = None
        self._misses = 0

    def observe(self, uid: str | None) -> str | None:
        if uid:
            try:
                normalized = normalize_uid(uid)
            except ValueError:
                normalized = ""
            if normalized:
                if normalized != self._present:
                    self._present = normalized
                    self._misses = 0
                    return normalized
                self._misses = 0
                return None
        if self._present is not None:
            self._misses += 1
            if self._misses >= self.miss_limit:
                self._present = None
                self._misses = 0
        return None


class LinuxI2C:
    """``/dev/i2c-N`` using the kernel I2C_SLAVE ioctl. No extra package."""

    def __init__(self, bus: int, address: int) -> None:
        self.path = f"/dev/i2c-{bus}"
        self.address = address
        self._fd: int | None = None

    def open(self) -> None:
        flags = os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            fd = os.open(self.path, flags)
        except OSError as exc:
            raise OSError(f"cannot open {self.path} ({exc})") from exc
        try:
            try:
                fcntl.ioctl(fd, _I2C_TIMEOUT, 100)  # 1 second
            except OSError:
                log.debug("I2C_TIMEOUT is not supported on %s", self.path)
            fcntl.ioctl(fd, _I2C_SLAVE, self.address)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise OSError(
                    "the NFC reader is already in use. Stop the service first: "
                    "sudo systemctl stop message-box"
                ) from exc
            except OSError:
                log.debug("could not lock %s", self.path, exc_info=True)
        except Exception:
            os.close(fd)
            raise
        self._fd = fd

    def write(self, data: bytes) -> None:
        if self._fd is None:
            raise OSError("i2c is not open")
        written = os.write(self._fd, data)
        if written != len(data):
            raise OSError(errno.EIO, f"short i2c write on {self.path}")

    def read(self, count: int) -> bytes:
        if self._fd is None:
            raise OSError("i2c is not open")
        return os.read(self._fd, count)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


class Pn532:
    """Command/response on a transport with ``read`` and ``write``."""

    def __init__(self, transport) -> None:
        self.transport = transport
        self._last_io = 0.0

    def wakeup(self) -> None:
        """Poke a sleeping PN532. The first write often NACKs; that is ignored."""
        try:
            self._write(WAKE, attempts=2)
        except OSError as exc:
            log.debug("PN532 wakeup write failed: %s", exc)

    def transact(self, command: bytes, *, timeout: float) -> bytes:
        """Send ``command`` and return the response payload after the TFI.

        Writes are retried when the chip NACKs. Reads that raise errno 121
        (Pi 5) or errno 5 (earlier Pi models) mean "not ready yet", not
        "the reader is gone".
        """
        self._write(build_frame(command), attempts=3)
        # The chip needs a moment to prepare the ACK. Reading immediately
        # is a NAK, which a Pi 5 surfaces as errno 121.
        time.sleep(_BUS_FREE_SECONDS)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            kind, payload = self._next_frame(deadline - time.monotonic())
            if kind == "ack":
                continue
            if kind == "nack":
                raise OSError("PN532 sent NACK")
            if not payload or payload[0] != PN532_TO_HOST:
                raise OSError(f"unexpected PN532 frame {payload.hex() if payload else ''}")
            return payload[1:]
        raise TimeoutError("PN532 did not answer")

    def _write(self, data: bytes, *, attempts: int) -> None:
        last: OSError | None = None
        for attempt in range(attempts):
            self._pause_for_bus()
            try:
                self.transport.write(data)
            except OSError as exc:
                self._last_io = time.monotonic()
                last = exc
                if not _retryable(exc) or attempt + 1 == attempts:
                    raise
                time.sleep(0.01 * (attempt + 1))
                continue
            self._last_io = time.monotonic()
            return
        if last is not None:
            raise last

    def _pause_for_bus(self) -> None:
        elapsed = time.monotonic() - self._last_io
        if self._last_io and elapsed < _BUS_FREE_SECONDS:
            time.sleep(_BUS_FREE_SECONDS - elapsed)

    def _read(self, count: int) -> bytes:
        """One I2C read. errno 121/5 propagates so the caller can retry it."""
        self._pause_for_bus()
        try:
            return self.transport.read(count)
        finally:
            self._last_io = time.monotonic()

    def _next_frame(self, timeout: float) -> tuple[str, bytes]:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            try:
                status = self._read(1)
            except OSError as exc:
                if not _retryable(exc):
                    raise
                time.sleep(0.005)
                continue
            if status == b"\x01":
                break
            time.sleep(0.005)
        else:
            raise TimeoutError("PN532 did not become ready")
        buf = b""
        while time.monotonic() < deadline:
            try:
                chunk = self._read(80)
            except OSError as exc:
                if not _retryable(exc):
                    raise
                time.sleep(0.005)
                continue
            if chunk.startswith(b"\x01") and not buf:
                chunk = chunk[1:]
            if not chunk:
                time.sleep(0.005)
                continue
            buf += chunk
            kind, payload, _rest = pop_frame(buf)
            if kind is not None:
                return kind, payload
        raise TimeoutError("PN532 frame was incomplete")


class Pn532Reader:
    """Open the chip, configure SAM, and poll for one ISO14443A UID."""

    def __init__(self, *, bus: int = 1, address: int = 0x24, transport=None) -> None:
        self.bus = bus
        self.address = address
        self._owned_transport = transport is None
        self.transport = transport if transport is not None else LinuxI2C(bus, address)
        self.chip: Pn532 | None = None

    def open(self) -> None:
        if self._owned_transport:
            self.transport.open()
        self.chip = Pn532(self.transport)
        last: BaseException | None = None
        for attempt in range(_INIT_ATTEMPTS):
            self.chip.wakeup()
            time.sleep(0.02)
            try:
                firmware = self.chip.transact(GET_FIRMWARE, timeout=0.8)
                if len(firmware) < 4 or firmware[0] != 0x03:
                    raise OSError(f"PN532 firmware response was {firmware.hex()}")
                sam = self.chip.transact(SAM_CONFIGURATION, timeout=0.8)
                if not sam or sam[0] != 0x15:
                    raise OSError(f"PN532 SAM configuration failed ({sam.hex() if sam else 'empty'})")
            except (OSError, TimeoutError) as exc:
                last = exc
                log.info("PN532 init attempt %s failed: %s", attempt + 1, exc)
                time.sleep(0.05)
                continue
            log.info(
                "PN532 on i2c-%s address 0x%02x firmware %s.%s",
                self.bus,
                self.address,
                firmware[2],
                firmware[3],
            )
            return
        detail = explain_i2c_failure(
            last or TimeoutError("PN532 did not answer"),
            bus=self.bus,
            address=self.address,
        )
        raise OSError(detail)

    def poll(self) -> str | None:
        if self.chip is None:
            raise OSError("PN532 is not open")
        # InAutoPoll waits about 150 ms on the chip, then returns.
        payload = self.chip.transact(IN_AUTO_POLL, timeout=1.5)
        return uid_from_autopoll(payload)

    def close(self) -> None:
        self.chip = None
        if self._owned_transport:
            self.transport.close()


class Pn532CardSource:
    """Background poller. ``start`` does not raise when the reader is missing."""

    def __init__(
        self,
        *,
        bus: int = 1,
        address: int = 0x24,
        reader_factory: Callable[[], Pn532Reader] | None = None,
        miss_limit: int = 3,
        retry_seconds: float = 5.0,
        idle_seconds: float = 0.05,
    ) -> None:
        self._factory = reader_factory or (lambda: Pn532Reader(bus=bus, address=address))
        self._miss_limit = miss_limit
        self._retry_seconds = retry_seconds
        self._idle_seconds = idle_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_card: Callable[[str], None] | None = None

    def start(self, on_card: Callable[[str], None]) -> None:
        self._on_card = on_card
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="messagebox-nfc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        detector = CardEdgeDetector(self._miss_limit)
        reader: Pn532Reader | None = None
        warned = False
        failures = 0
        while not self._stop.is_set():
            if reader is None:
                try:
                    reader = self._factory()
                    reader.open()
                except Exception as exc:
                    if reader is not None:
                        self._close_reader(reader)
                    reader = None
                    if not warned:
                        log.warning(
                            "NFC reader is not available (%s). The box still works as one chat.",
                            exc,
                        )
                        warned = True
                    else:
                        log.debug("NFC reader still unavailable: %s", exc)
                    if self._stop.wait(self._retry_seconds):
                        break
                    continue
                warned = False
                failures = 0
                log.info("NFC reader is ready")
            try:
                uid = reader.poll()
                failures = 0
            except TimeoutError:
                failures += 1
                uid = None
                if failures >= 3:
                    log.warning("NFC reader stopped answering; will retry")
                    self._close_reader(reader)
                    reader = None
                    failures = 0
                    if self._stop.wait(self._retry_seconds):
                        break
                    continue
            except Exception as exc:
                log.warning("NFC poll failed (%s); will retry", exc)
                self._close_reader(reader)
                reader = None
                if self._stop.wait(self._retry_seconds):
                    break
                continue
            presented = detector.observe(uid)
            if presented is not None and self._on_card is not None:
                try:
                    self._on_card(presented)
                except Exception:
                    log.exception("card callback failed")
            if self._stop.wait(self._idle_seconds):
                break
        if reader is not None:
            self._close_reader(reader)

    @staticmethod
    def _close_reader(reader: Pn532Reader) -> None:
        try:
            reader.close()
        except Exception:
            log.debug("closing the NFC reader failed", exc_info=True)


def format_scan(uid: str) -> str:
    """Text for ``--scan-card``. The chat id is a placeholder, not a real chat."""
    return (
        f"UID: {uid}\n"
        "\n"
        "[[cards]]\n"
        f'uid = "{uid}"\n'
        "chat_id = -1000000000001\n"
        'announce = "Chat name"\n'
        "\n"
        "Replace chat_id with one id from allowed_chat_ids.\n"
        "announce is what the speaker says. Add announce_file to play a wav instead.\n"
    )


def scan_card(bus: int, address: int, *, timeout: float = 60.0) -> int:
    """Print the next card UID. 0 on success, 1 if the reader or the tap fails.

    Stop the service first. Two pollers on one PN532 corrupt each other's frames.
    """
    print("Hold a card on the reader. Waiting up to 60 seconds.")
    print("If message-box is running, stop it first: sudo systemctl stop message-box")
    reader = Pn532Reader(bus=bus, address=address)
    try:
        reader.open()
    except Exception as exc:
        print(f"NFC reader is not available: {exc}", file=sys.stderr)
        reader.close()
        return 1
    detector = CardEdgeDetector(miss_limit=2)
    deadline = time.monotonic() + timeout
    poll_errors = 0
    try:
        while time.monotonic() < deadline:
            try:
                uid = reader.poll()
                poll_errors = 0
            except TimeoutError:
                uid = None
            except OSError as exc:
                poll_errors += 1
                uid = None
                if poll_errors >= 3:
                    print(explain_i2c_failure(exc, bus=bus, address=address), file=sys.stderr)
                    return 1
            presented = detector.observe(uid)
            if presented:
                print(format_scan(presented), end="")
                return 0
    except KeyboardInterrupt:
        return 1
    finally:
        reader.close()
    print("No card was tapped.", file=sys.stderr)
    return 1
