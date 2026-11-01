"""Per-chat inbox: legacy files, replay retention, and restart."""

import json

from messagebox.button_machine import ButtonMachine
from messagebox.coordinator import Coordinator, EffectKind
from messagebox.queue_store import Inbox

CHAT = -1000000000001
OTHER = -1000000000002


def _write_legacy(root, item_id, chat_id, payload, *, heard=False, heard_at=0, heard_seq=0):
    root.mkdir(parents=True, exist_ok=True)
    name = f"{item_id}.ogg"
    (root / name).write_bytes(payload)
    meta = {"id": item_id, "filename": name, "kind": "voice"}
    if chat_id is not None:
        meta["source_chat_id"] = chat_id
    if heard:
        meta["heard"] = True
        meta["heard_at"] = heard_at
        meta["heard_seq"] = heard_seq
    (root / f"{item_id}.json").write_text(json.dumps(meta), encoding="utf-8")


def _tap(coord, at):
    coord.press(at)
    return coord.release(at + 0.2)


def test_legacy_notes_stay_on_their_stored_chat(tmp_path):
    root = tmp_path / "inbox"
    _write_legacy(root, "00000001", OTHER, b"older-other")
    _write_legacy(root, "00000002", CHAT, b"from-chat")
    _write_legacy(root, "00000003", CHAT, b"later-chat")
    _write_legacy(root, "00000004", None, b"no-chat")
    (root / ".seq").write_text("4\n")
    stored = (root / "00000002.json").read_text(encoding="utf-8")

    inbox = Inbox(root)
    assert inbox.prepare(CHAT) == 1
    assert (root / "00000002.json").read_text(encoding="utf-8") == stored
    assert (root / "00000001.ogg").read_bytes() == b"older-other"
    assert (root / "00000004.ogg").read_bytes() == b"no-chat"

    # A second open does not attribute them again, and does not drop unheard audio.
    assert Inbox(root).prepare(CHAT) == 0
    choice = inbox.next_play(CHAT)
    assert choice is not None and choice.replay is False
    assert choice.item.id == "00000002"
    assert choice.item.path.read_bytes() == b"from-chat"
    other = inbox.next_play(OTHER)
    assert other is not None and other.item.id == "00000001"
    orphan = inbox.get("00000004")
    assert orphan is not None
    assert orphan.meta["source_chat_id"] == CHAT

    added = inbox.enqueue_bytes(b"new", meta={"source_chat_id": CHAT})
    assert added.id == "00000005"
    assert (root / "00000002.ogg").read_bytes() == b"from-chat"

    focus = {"chat_id": CHAT}
    coord = Coordinator(
        ButtonMachine(hold_seconds=1.0, min_record_seconds=1.0, max_record_seconds=5.0),
        inbox,
        focused_chat=lambda: focus["chat_id"],
    )
    effects = _tap(coord, 0)
    play = next(effect for effect in effects if effect.kind is EffectKind.PLAY)
    assert play.item_id == "00000002"
    assert play.replay is False
    # The other chat's note is still on disk and still unheard.
    assert inbox.get("00000001").meta.get("heard") is not True


def test_one_heard_note_per_chat_survives_reopen(tmp_path):
    root = tmp_path / "inbox"
    _write_legacy(root, "00000001", CHAT, b"old-heard", heard=True, heard_at=10, heard_seq=1)
    _write_legacy(root, "00000002", CHAT, b"latest-heard", heard=True, heard_at=20, heard_seq=2)
    _write_legacy(root, "00000003", CHAT, b"still-waiting")
    _write_legacy(root, "00000004", OTHER, b"other-heard", heard=True, heard_at=15, heard_seq=1)
    (root / ".seq").write_text("4\n")

    inbox = Inbox(root)
    inbox.prepare(CHAT)
    assert inbox.get("00000001") is None
    assert inbox.get("00000002").path.read_bytes() == b"latest-heard"
    assert inbox.get("00000003").path.read_bytes() == b"still-waiting"
    assert inbox.get("00000004").path.read_bytes() == b"other-heard"

    reopened = Inbox(root)
    waiting = reopened.next_play(CHAT)
    assert waiting is not None and waiting.replay is False and waiting.item.id == "00000003"
    reopened.mark_heard("00000003")
    assert reopened.get("00000002") is None
    assert reopened.get("00000003").path.read_bytes() == b"still-waiting"

    again = Inbox(root)
    replay = again.next_play(CHAT)
    assert replay is not None and replay.replay is True and replay.item.id == "00000003"
    assert again.next_play(OTHER).item.id == "00000004"
    assert again.next_play(OTHER).replay is True


def test_oldest_unheard_wins_inside_one_chat(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    other = inbox.enqueue_bytes(b"other-first", meta={"source_chat_id": OTHER})
    first = inbox.enqueue_bytes(b"a-old", meta={"source_chat_id": CHAT})
    second = inbox.enqueue_bytes(b"a-new", meta={"source_chat_id": CHAT})
    choice = inbox.next_play(CHAT)
    assert choice is not None
    assert choice.item.id == first.id
    assert choice.replay is False
    inbox.mark_heard(first.id)
    assert inbox.next_play(CHAT).item.id == second.id
    assert inbox.get(other.id) is not None
