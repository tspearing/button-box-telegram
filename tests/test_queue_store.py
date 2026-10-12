"""The on-disk FIFO survives a new process, which is what a reboot is."""

import json

from messagebox.queue_store import DiskQueue


def test_fifo_order_and_remove(tmp_path):
    queue = DiskQueue(tmp_path / "inbox")
    first = queue.enqueue_bytes(b"one", meta={"sender_label": "sender-a"})
    second = queue.enqueue_bytes(b"two", meta={"sender_label": "sender-b"})
    assert [item.meta["sender_label"] for item in queue.list_items()] == ["sender-a", "sender-b"]
    assert queue.peek().id == first.id
    assert len(queue) == 2
    queue.remove(first.id)
    assert queue.peek().id == second.id
    assert queue.peek().path.read_bytes() == b"two"
    queue.remove(second.id)
    assert queue.peek() is None
    assert len(queue) == 0


def test_reload_sees_the_same_queue(tmp_path):
    root = tmp_path / "inbox"
    first = DiskQueue(root)
    item = first.enqueue_bytes(b"audio-bytes", suffix=".ogg", meta={"kind": "voice"})
    second = DiskQueue(root)
    assert len(second) == 1
    assert second.peek().id == item.id
    assert second.peek().path.read_bytes() == b"audio-bytes"
    again = second.enqueue_bytes(b"later")
    assert [item.id for item in second.list_items()] == [item.id, again.id]
    assert int(again.id) == int(item.id) + 1


def test_source_file_is_copied(tmp_path):
    source = tmp_path / "note.ogg"
    source.write_bytes(b"ogg-data")
    queue = DiskQueue(tmp_path / "out")
    item = queue.enqueue(source, {"target_chat_id": -100})
    source.unlink()
    assert item.path.read_bytes() == b"ogg-data"
    assert item.meta["target_chat_id"] == -100
    assert item.meta["id"] == item.id


def test_partial_and_broken_files_are_not_playable(tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    (root / ".00000009.partial").write_bytes(b"torn")
    (root / "00000003.json").write_text("{", encoding="utf-8")
    (root / "00000004.json").write_text(
        json.dumps({"id": "00000004", "filename": "00000004.ogg"}),
        encoding="utf-8",
    )
    queue = DiskQueue(root)
    assert list(queue.list_items()) == []
    real = queue.enqueue_bytes(b"ok")
    assert queue.peek().id == real.id


def test_head_waits_on_the_oldest_even_if_a_later_item_is_ready(tmp_path):
    queue = DiskQueue(tmp_path / "outbox")
    oldest = queue.enqueue_bytes(b"a", meta={"next_attempt_at": 500})
    queue.enqueue_bytes(b"b", meta={"next_attempt_at": 0})
    state, item = queue.head_state(100)
    assert state == "wait"
    assert item is not None and item.id == oldest.id
    state, item = queue.head_state(500)
    assert state == "ready"
    assert item is not None and item.id == oldest.id


def test_update_persists(tmp_path):
    root = tmp_path / "outbox"
    queue = DiskQueue(root)
    item = queue.enqueue_bytes(b"a", meta={"attempts": 0})
    queue.update(item.id, attempts=2, next_attempt_at=80)
    reloaded = DiskQueue(root).get(item.id)
    assert reloaded is not None
    assert reloaded.meta["attempts"] == 2
    assert reloaded.meta["next_attempt_at"] == 80


def test_remove_is_idempotent(tmp_path):
    queue = DiskQueue(tmp_path / "inbox")
    queue.remove("00000001")
    item = queue.enqueue_bytes(b"a")
    queue.remove(item.id)
    queue.remove(item.id)
    assert len(queue) == 0


def test_caller_cannot_override_the_stored_id(tmp_path):
    queue = DiskQueue(tmp_path / "inbox")
    item = queue.enqueue_bytes(b"a", meta={"id": "99999999", "filename": "nope.ogg"})
    assert item.meta["id"] == item.id
    assert item.meta["filename"] == item.path.name
    assert DiskQueue(tmp_path / "inbox").peek().id == item.id
