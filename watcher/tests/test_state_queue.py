import json

from watcher.ports import Payload
from watcher.queue import DiskQueue
from watcher.state import State


def payload(n=1, files=None, text="hi"):
    return Payload(chat_id="-100", message_id=str(n), album_id=None, date="2026-10-06T10:00:00+00:00", text=text,
                   files=files if files is not None else [("a.pdf", b"%PDF-" + bytes([n]))])


def test_state_defaults_save_atomically_and_reload(tmp_path):
    path = tmp_path / "state.json"
    state = State(path)
    assert state.chat("-100").last_id == 0
    state.chat("-100").last_id = 42
    state.chat("-100").gone = True
    state.save()
    assert not list(tmp_path.glob("*.tmp"))
    again = State(path)
    assert again.chat("-100").last_id == 42 and again.chat("-100").gone is True


def test_a_corrupt_state_file_starts_empty_and_keeps_a_copy(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    state = State(path)
    assert state.chat("-1").last_id == 0 and (tmp_path / "state.json.bad").exists()


def test_a_crash_before_the_rename_leaves_the_old_state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    state = State(path)
    state.chat("-1").last_id = 5
    state.save()
    state.chat("-1").last_id = 9
    monkeypatch.setattr("os.replace", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    try:
        state.save()
    except OSError:
        pass
    assert json.loads(path.read_text(encoding="utf-8"))["chats"]["-1"]["last_id"] == 5


def test_the_queue_keeps_order_files_and_survives_a_restart(tmp_path):
    q = DiskQueue(tmp_path / "queue")
    q.put(payload(1))
    q.put(payload(2, text="second"))
    assert len(q) == 2
    reopened = DiskQueue(tmp_path / "queue")
    first = reopened.oldest()
    assert first.payload() == payload(1)
    reopened.remove(first)
    assert reopened.oldest().payload().text == "second" and len(reopened) == 1


def test_a_half_written_item_is_never_delivered(tmp_path):
    q = DiskQueue(tmp_path / "queue")
    (tmp_path / "queue" / "000000000001.tmp").mkdir()
    (tmp_path / "queue" / "000000000001.tmp" / "meta.json").write_text("{", encoding="utf-8")
    assert q.oldest() is None and len(q) == 0
    q.put(payload(3))
    assert len(q) == 1 and q.oldest().payload().message_id == "3"


def test_a_half_written_item_left_by_a_crash_is_cleaned_on_restart(tmp_path):
    (tmp_path / "queue").mkdir()
    (tmp_path / "queue" / "000000000001.tmp").mkdir()
    DiskQueue(tmp_path / "queue")
    assert not list((tmp_path / "queue").glob("*.tmp"))


def test_the_queue_reports_full_by_items_or_bytes(tmp_path):
    q = DiskQueue(tmp_path / "queue", max_items=2, max_bytes=10_000)
    q.put(payload(1))
    assert not q.full()
    q.put(payload(2))
    assert q.full()
    small = DiskQueue(tmp_path / "other", max_items=500, max_bytes=50)
    small.put(payload(1, files=[("big.pdf", b"%PDF-" + b"x" * 100)]))
    assert small.full() and small.size_bytes() >= 100


def test_sequence_numbers_keep_growing_after_removals(tmp_path):
    q = DiskQueue(tmp_path / "queue")
    q.put(payload(1))
    q.remove(q.oldest())
    q.put(payload(2))
    assert q.oldest().payload().message_id == "2"
