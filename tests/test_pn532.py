"""PN532 frames and the card thread, with no I2C hardware."""

import subprocess
import time

import pytest

from messagebox.inputs.pn532 import (
    IN_AUTO_POLL,
    CardEdgeDetector,
    Pn532,
    Pn532CardSource,
    Pn532Reader,
    _unknown_targets,
    build_frame,
    encode_frame,
    explain_i2c_failure,
    format_scan,
    pop_frame,
    uid_from_autopoll,
)


class ChunkTransport:
    def __init__(self, reads: list[bytes]) -> None:
        self.reads = list(reads)
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def read(self, count: int) -> bytes:
        if not self.reads:
            return b""
        chunk = self.reads[0]
        take, rest = chunk[:count], chunk[count:]
        if rest:
            self.reads[0] = rest
        else:
            self.reads.pop(0)
        return take


def _ready(frame: bytes) -> list[bytes]:
    ack = bytes.fromhex("0000ff00ff00")
    return [b"\x01", b"\x01" + ack, b"\x01", b"\x01" + frame]


def test_firmware_command_frame_matches_the_manual():
    # LEN 2, LCS 0xFE, body D4 02, DCS 0x2A.
    assert build_frame(b"\x02") == bytes.fromhex("0000ff02fed4022a00")


# Captured on the Pi from NTAG215 cards. InAutoPoll, not personal data.
# 0x61, NbTg, then Type 0x10, Length 0x0c, then Tg, SENS_RES 0044,
# SEL_RES 00, NFCIDLength 07, UID.
_TWO_CARDS = "6102100c010044000704e350d95f6180100c020044000704f269203f6180"
_ONE_CARD_SHORT = "6101010044000704e350d95f6180"
_ONE_CARD_LONG = "6101100c010044000704505da44f6180"


def test_autopoll_uid_for_a_seven_byte_tag():
    assert uid_from_autopoll(bytes.fromhex(_ONE_CARD_LONG)) == "04505DA44F6180"
    assert uid_from_autopoll(bytes.fromhex("6100")) is None


def test_pi_frames_yield_the_card_uid(caplog):
    _unknown_targets.clear()
    caplog.set_level("INFO", logger="messagebox.nfc")
    assert uid_from_autopoll(bytes.fromhex(_ONE_CARD_SHORT)) == "04E350D95F6180"
    assert uid_from_autopoll(bytes.fromhex(_ONE_CARD_LONG)) == "04505DA44F6180"
    assert uid_from_autopoll(bytes.fromhex(_TWO_CARDS)) == "04E350D95F6180"
    assert "using the first" in caplog.text
    assert "Hold one card at a time" in caplog.text
    # The same two-card frame must not log again on the next poll.
    caplog.clear()
    assert uid_from_autopoll(bytes.fromhex(_TWO_CARDS)) == "04E350D95F6180"
    assert caplog.records == []


def test_four_and_ten_byte_uids_with_and_without_the_type_header():
    four = bytes.fromhex("922E5832")
    ten = bytes.fromhex("04112233445566778899")
    assert uid_from_autopoll(_wrapped(four, sens=bytes.fromhex("0400"), sel=0x08)) == "922E5832"
    assert uid_from_autopoll(_raw(four, sens=bytes.fromhex("0400"), sel=0x08)) == "922E5832"
    assert uid_from_autopoll(_wrapped(ten)) == "04112233445566778899"
    assert uid_from_autopoll(_raw(ten)) == "04112233445566778899"
    # The manual's two-target example: a 4-byte Mifare card, then a DEP target.
    manual = bytes.fromhex(
        "610210090104000804922e5832"
        "4018020008400408123456001122334455667788990000000901"
    )
    assert uid_from_autopoll(manual) == "922E5832"


def test_atqa_first_layout_is_not_read_as_a_uid():
    # Old guess: Type, Length, ATQA, SAK, UID length, UID. The length byte
    # landed on SEL_RES for a real card. This synthetic frame must not
    # come back as a 4-byte UID taken from the middle.
    _unknown_targets.clear()
    assert uid_from_autopoll(bytes.fromhex("6101100b4400000704a1b2c3d4e5f6")) is None
    assert uid_from_autopoll(bytes.fromhex("6101ff")) is None


def test_a_split_read_does_not_explain_the_short_frame():
    """The short Pi frame is a real response, not ``10 0c`` lost in transit.

    Those two bytes are Type and Length. Cutting them out of a valid long
    frame, or inserting the next read's ready byte there, fails the
    checksum. ``pop_frame`` will not hand the short payload to the parser.
    """
    long_payload = bytes.fromhex(_ONE_CARD_LONG)
    frame = encode_frame(0xD5, long_payload)
    kind, body, _rest = pop_frame(b"\x01" + frame)
    assert kind == "data"
    assert body[1:] == long_payload
    marker = bytes.fromhex("100c")
    at = frame.index(marker)
    assert pop_frame(frame[:at] + frame[at + 2 :])[0] is None
    assert pop_frame(frame[:at] + b"\x01" + frame[at:])[0] is None
    # The short capture is its own valid frame, and it parses.
    short = bytes.fromhex(_ONE_CARD_SHORT)
    kind, body, _rest = pop_frame(encode_frame(0xD5, short))
    assert kind == "data" and body[1:] == short
    assert uid_from_autopoll(short) == "04E350D95F6180"


def _wrapped(uid: bytes, *, sens: bytes = bytes.fromhex("0044"), sel: int = 0) -> bytes:
    record = bytes((1,)) + sens + bytes((sel, len(uid))) + uid
    return bytes((0x61, 0x01, 0x10, len(record))) + record


def _raw(uid: bytes, *, sens: bytes = bytes.fromhex("0044"), sel: int = 0) -> bytes:
    return bytes((0x61, 0x01, 0x01)) + sens + bytes((sel, len(uid))) + uid


def test_reader_poll_uses_scripted_frames():
    reads: list[bytes] = []
    reads += _ready(encode_frame(0xD5, bytes.fromhex("0332010607")))
    reads += _ready(encode_frame(0xD5, bytes.fromhex("15")))
    reads += _ready(encode_frame(0xD5, bytes.fromhex(_ONE_CARD_LONG)))
    reads += _ready(encode_frame(0xD5, bytes.fromhex(_ONE_CARD_SHORT)))
    reads += _ready(encode_frame(0xD5, bytes.fromhex("6100")))
    transport = ChunkTransport(reads)
    reader = Pn532Reader(bus=1, address=0x24, transport=transport)
    reader.open()
    assert reader.poll() == "04505DA44F6180"
    assert reader.poll() == "04E350D95F6180"
    assert reader.poll() is None
    assert transport.writes[0].startswith(b"\x55\x55")
    assert transport.writes[1] == build_frame(b"\x02")
    assert IN_AUTO_POLL == bytes((0x60, 0x01, 0x01, 0x00))
    assert IN_AUTO_POLL in transport.writes[-1]


class _NakThen(ChunkTransport):
    """Raise errno 121 a few times, then behave like a scripted reader.

    A Pi 5 does this when the PN532 NACKs a read or a write while it is
    waking up. The status byte is never delivered; the syscall fails.
    """

    def __init__(self, *, read_naks: int = 0, write_naks: int = 0, reads: list[bytes] | None = None):
        super().__init__(reads or [])
        self.read_naks = read_naks
        self.write_naks = write_naks

    def write(self, data: bytes) -> None:
        if self.write_naks:
            self.write_naks -= 1
            raise OSError(121, "Remote I/O error")
        super().write(data)

    def read(self, count: int) -> bytes:
        if self.read_naks:
            self.read_naks -= 1
            raise OSError(121, "Remote I/O error")
        return super().read(count)


def test_transact_retries_a_pi5_read_nak():
    frame = encode_frame(0xD5, bytes.fromhex("0332010607"))
    ack = bytes.fromhex("0000ff00ff00")
    transport = _NakThen(
        read_naks=2,
        reads=[b"\x01", b"\x01" + ack, b"\x01", b"\x01" + frame],
    )
    chip = Pn532(transport)
    assert chip.transact(b"\x02", timeout=1.0) == bytes.fromhex("0332010607")
    assert transport.writes == [build_frame(b"\x02")]


def test_transact_retries_a_wakeup_style_write_nak():
    frame = encode_frame(0xD5, bytes.fromhex("0332010607"))
    ack = bytes.fromhex("0000ff00ff00")
    transport = _NakThen(
        write_naks=1,
        reads=[b"\x01", b"\x01" + ack, b"\x01", b"\x01" + frame],
    )
    chip = Pn532(transport)
    assert chip.transact(b"\x02", timeout=1.0) == bytes.fromhex("0332010607")
    assert transport.writes == [build_frame(b"\x02")]


def test_open_survives_naks_and_reports_a_dead_bus(monkeypatch):
    reads: list[bytes] = []
    reads += _ready(encode_frame(0xD5, bytes.fromhex("0332010607")))
    reads += _ready(encode_frame(0xD5, bytes.fromhex("15")))
    flaky = _NakThen(read_naks=3, write_naks=1, reads=reads)
    reader = Pn532Reader(bus=1, address=0x24, transport=flaky)
    reader.open()
    assert any(build_frame(b"\x02") == written for written in flaky.writes)

    class Dead:
        def write(self, data: bytes) -> None:
            raise OSError(121, "Remote I/O error")

        def read(self, count: int) -> bytes:
            raise OSError(121, "Remote I/O error")

    monkeypatch.setattr("messagebox.inputs.pn532.read_i2c_clock_hz", lambda _bus: 100000)
    with pytest.raises(OSError, match="dtparam=i2c_arm_baudrate=10000") as raised:
        Pn532Reader(bus=1, address=0x24, transport=Dead()).open()
    text = str(raised.value)
    assert "121" in text or "Remote I/O" in text
    assert "sudo reboot" in text
    monkeypatch.setattr("messagebox.inputs.pn532.read_i2c_clock_hz", lambda _bus: 10000)
    with pytest.raises(OSError, match="already 10000") as steady:
        Pn532Reader(bus=1, address=0x24, transport=Dead()).open()
    assert "sudo reboot" not in str(steady.value)


def test_i2c_failure_text_names_the_clock_fix(monkeypatch):
    monkeypatch.setattr("messagebox.inputs.pn532.read_i2c_clock_hz", lambda _bus: None)
    text = explain_i2c_failure(OSError(121, "Remote I/O error"), bus=1, address=0x24)
    assert "0x24" in text
    assert "i2c-1" in text
    assert "dtparam=i2c_arm_baudrate=10000" in text
    assert "home" not in text.lower()


def test_baudrate_parser_does_not_confuse_100000_with_10000(tmp_path):
    script = r'''
    last=$(grep -E '^[[:space:]]*dtparam=i2c_arm_baudrate=' "$1" | tail -n 1 || true)
    value="${last##*baudrate=}"
    value="${value%%[^0-9]*}"
    printf '%s' "$value"
    '''
    fast = tmp_path / "fast.txt"
    fast.write_text("dtparam=i2c_arm=on\ndtparam=i2c_arm_baudrate=100000\n", encoding="utf-8")
    slow = tmp_path / "slow.txt"
    slow.write_text("# dtparam=i2c_arm_baudrate=400000\ndtparam=i2c_arm_baudrate=10000\n", encoding="utf-8")
    assert _baud_value(script, fast) == "100000"
    assert _baud_value(script, slow) == "10000"


def _baud_value(script: str, path) -> str:
    return subprocess.check_output(["bash", "-c", script, "bash", str(path)], text=True)


def test_transact_skips_a_not_ready_byte():
    frame = encode_frame(0xD5, bytes.fromhex("0332010607"))
    ack = bytes.fromhex("0000ff00ff00")
    transport = ChunkTransport([b"\x00", b"\x01", b"\x01" + ack, b"\x01", frame])
    chip = Pn532(transport)
    assert chip.transact(b"\x02", timeout=1.0) == bytes.fromhex("0332010607")


def test_edge_detector_announces_once_per_presentation():
    edge = CardEdgeDetector(miss_limit=3)
    assert edge.observe("04:a1:b2:c3:d4:e5:f6") == "04A1B2C3D4E5F6"
    assert edge.observe("04A1B2C3D4E5F6") is None
    assert edge.observe(None) is None
    assert edge.observe(None) is None
    assert edge.observe("04A1B2C3D4E5F6") is None
    assert edge.observe(None) is None
    assert edge.observe(None) is None
    assert edge.observe(None) is None
    assert edge.observe("04A1B2C3D4E5F6") == "04A1B2C3D4E5F6"
    assert edge.observe("04B1C2D3E4F506") == "04B1C2D3E4F506"


def test_card_thread_reports_each_presentation_and_survives_a_missing_reader():
    seen: list[str] = []
    polls: list[object] = ["04A1B2C3D4E5F6", "04A1B2C3D4E5F6", None, None, "04A1B2C3D4E5F6"]

    class Reader:
        def open(self) -> None:
            return None

        def poll(self):
            if not polls:
                return None
            return polls.pop(0)

        def close(self) -> None:
            return None

    source = Pn532CardSource(
        reader_factory=lambda: Reader(),
        miss_limit=2,
        retry_seconds=0.01,
        idle_seconds=0.01,
    )
    source.start(seen.append)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and len(seen) < 2:
        time.sleep(0.02)
    source.stop()
    assert seen == ["04A1B2C3D4E5F6", "04A1B2C3D4E5F6"]

    class Missing:
        def open(self) -> None:
            raise OSError("cannot open /dev/i2c-1")

        def poll(self):
            return None

        def close(self) -> None:
            return None

    quiet = Pn532CardSource(
        reader_factory=lambda: Missing(),
        retry_seconds=0.02,
        idle_seconds=0.01,
    )
    quiet.start(seen.append)
    time.sleep(0.08)
    quiet.stop()


def test_scan_text_uses_a_placeholder_chat_id():
    text = format_scan("04A1B2C3D4E5F6")
    assert 'uid = "04A1B2C3D4E5F6"' in text
    assert "chat_id = -1000000000001" in text
    assert 'announce = "Chat name"' in text
