import asyncio
import logging
from datetime import datetime, timedelta, timezone

from watcher.amide_client import Delivery
from watcher.config import Config
from watcher.ports import Access, Attachment, Group, TgMessage
from watcher.queue import DiskQueue
from watcher.runner import ALBUM_SETTLE_SECONDS, Runner
from watcher.state import State
from watcher.tests.fakes import FakeAmide, FakeTelegram

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
SECRET_TEXT = "Zorvex ZX10 10mg*10vials $60 SECRET-PRICE-TEXT"


def msg(chat="-100", mid=1, text="", minutes_ago=60, grouped=None, files=()):
    return TgMessage(chat_id=chat, message_id=mid, date=NOW - timedelta(minutes=minutes_ago), text=text, grouped_id=grouped,
                     attachments=[Attachment(*f) for f in files])


def build(tmp_path, tg=None, amide=None, **cfg):
    config = Config(amide_url="http://x", amide_token="test-token", telegram_api_id=1234, telegram_api_hash="fakehash", home=tmp_path, **cfg)
    tg = tg or FakeTelegram()
    amide = amide or FakeAmide(watch=["-100"])
    state, queue = State(tmp_path / "state.json"), DiskQueue(tmp_path / "queue")
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
    runner = Runner(tg, amide, state, queue, config, now=lambda: NOW, sleep=sleep, log=logging.getLogger("watcher-test"))
    return runner, tg, amide, state, queue, slept


def poll(runner):
    asyncio.run(runner.poll_once())


# ---------------------------------------------------------------- registering and what is read

def test_every_group_is_registered_by_title_but_only_listed_groups_are_read(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "Acme group"), Group("-200", "Unmapped group")],
                      messages={"-100": [msg("-100", 1, "hello")], "-200": [msg("-200", 1, "secret chatter")]})
    runner, tg, amide, *_ = build(tmp_path, tg=tg)
    poll(runner)
    assert amide.registered == [("-100", "Acme group"), ("-200", "Unmapped group")]
    assert [c for c in tg.calls if c[0] == "messages_since"] == [("messages_since", "-100")]          # the unmapped group is never read
    assert {name for name, *_ in tg.calls} <= {"list_groups", "messages_since", "download", "check_access"}


def test_a_group_that_amide_stops_listing_stops_being_read(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "Acme group")], messages={"-100": [msg("-100", 1, "hello")]})
    runner, tg, amide, *_ = build(tmp_path, tg=tg, amide=FakeAmide(watch=[]))
    poll(runner)
    assert not [c for c in tg.calls if c[0] == "messages_since"] and amide.sent == []


# ---------------------------------------------------------------- messages

def test_a_text_message_and_a_file_message_are_delivered_in_order_with_their_context(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "Acme group")], messages={"-100": [
        msg("-100", 5, SECRET_TEXT, 50), msg("-100", 6, "USA warehouse", 40, files=[("list.pdf", "pdf", 100)])]},
        downloads={("-100", 6, "list.pdf"): b"%PDF-1.4 data"})
    runner, tg, amide, state, queue, _ = build(tmp_path, tg=tg)
    poll(runner)
    assert [(p.message_id, p.text, [n for n, _ in p.files], p.date) for p in amide.sent] == [
        ("5", SECRET_TEXT, [], (NOW - timedelta(minutes=50)).isoformat()),
        ("6", "USA warehouse", ["list.pdf"], (NOW - timedelta(minutes=40)).isoformat())]
    assert amide.sent[1].files[0][1] == b"%PDF-1.4 data" and len(queue) == 0 and state.chat("-100").last_id == 6


def test_unwanted_and_oversized_attachments_and_empty_messages_are_skipped(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], messages={"-100": [
        msg("-100", 1, "", 50, files=[("big.pdf", "pdf", 26 * 1024 * 1024)]), msg("-100", 2, "", 40), msg("-100", 3, "ok", 30)]})
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg)
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["3"] and not [c for c in tg.calls if c[0] == "download"]
    assert state.chat("-100").last_id == 3


def test_the_first_read_goes_back_backfill_days_and_later_reads_continue_from_the_last_id(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], messages={"-100": [msg("-100", 1, "old", 60 * 24 * 10), msg("-100", 2, "recent", 60 * 24 * 2)]})
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg)
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["2"]                                  # 7 days back by default: the 10-day-old one is not read
    tg.messages["-100"].append(msg("-100", 3, "new", 5))
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["2", "3"]
    assert ("messages_since", "-100", 2) in tg.detailed


def test_an_album_is_sent_as_one_message_once_it_has_settled(tmp_path):
    files = [("1.jpg", "image", 10), ("2.jpg", "image", 10)]
    tg = FakeTelegram(groups=[Group("-100", "g")], messages={"-100": [
        msg("-100", 7, "caption", 1, grouped=99, files=files[:1]), msg("-100", 8, "", 0, grouped=99, files=files[1:])]},
        downloads={("-100", 7, "1.jpg"): b"a", ("-100", 8, "2.jpg"): b"b"})
    tg.messages["-100"][0].date = NOW - timedelta(seconds=3)
    tg.messages["-100"][1].date = NOW - timedelta(seconds=2)                              # the last photo arrived 2 s ago: not settled
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg)
    poll(runner)
    assert amide.sent == [] and state.chat("-100").last_id == 0                          # held back, nothing lost
    for m in tg.messages["-100"]:
        m.date = NOW - timedelta(seconds=ALBUM_SETTLE_SECONDS + 1)
    poll(runner)
    assert len(amide.sent) == 1 and amide.sent[0].album_id == "99" and amide.sent[0].message_id == "7"
    assert [n for n, _ in amide.sent[0].files] == ["1.jpg", "2.jpg"] and amide.sent[0].text == "caption"


# ---------------------------------------------------------------- delivery

def test_when_amide_is_down_messages_wait_in_order_then_deliver_with_backoff(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], messages={"-100": [msg("-100", 1, "one", 50), msg("-100", 2, "two", 40)]})
    amide = FakeAmide(watch=["-100"], outcomes=[Delivery("retry", "HTTP 503")])
    runner, tg, amide, state, queue, _ = build(tmp_path, tg=tg, amide=amide)
    poll(runner)
    assert len(queue) == 2 and amide.sent == [] and state.chat("-100").last_id == 2        # read and safely queued, not lost
    poll(runner)                                                                            # still inside the first backoff wait
    assert amide.attempts == 1
    runner.now = lambda: NOW + timedelta(seconds=31)
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["1", "2"] and len(queue) == 0


def test_a_rejected_message_is_dropped_and_a_refused_token_pauses_without_a_crash_loop(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], messages={"-100": [msg("-100", 1, "one", 50), msg("-100", 2, "two", 40)]})
    amide = FakeAmide(watch=["-100"], outcomes=[Delivery("drop", "HTTP 422"), Delivery("auth", "token refused")])
    runner, tg, amide, state, queue, _ = build(tmp_path, tg=tg, amide=amide)
    poll(runner)
    assert len(queue) == 1 and runner.paused is True                                        # the first was dropped, the second waits
    attempts = amide.attempts
    poll(runner)
    assert amide.attempts == attempts + 1 and len(queue) == 0                               # token works again: the waiting message goes out


def test_a_full_queue_stops_reading_but_loses_nothing(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], messages={"-100": [msg("-100", n, f"m{n}", 100 - n) for n in range(1, 6)]})
    amide = FakeAmide(watch=["-100"], outcomes=[Delivery("retry")] * 50)
    runner, tg, amide, state, queue, _ = build(tmp_path, tg=tg, amide=amide)
    queue.max_items = 3
    poll(runner)
    assert len(queue) == 3 and state.chat("-100").last_id == 3                              # stopped at the cap; 4 and 5 are still in Telegram
    queue.max_items = 500
    amide.outcomes = iter([])
    runner.now = lambda: NOW + timedelta(hours=1)
    poll(runner)
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["1", "2", "3", "4", "5"]


def test_nothing_secret_is_ever_logged(tmp_path, caplog):
    tg = FakeTelegram(groups=[Group("-100", "Acme group")], messages={"-100": [msg("-100", 1, SECRET_TEXT, 50)]})
    runner, *_ = build(tmp_path, tg=tg, amide=FakeAmide(watch=["-100"], outcomes=[Delivery("retry", "HTTP 503")]))
    with caplog.at_level(logging.DEBUG):
        poll(runner)
    text = caplog.text + "".join(r.getMessage() for r in caplog.records)
    assert "SECRET-PRICE-TEXT" not in text and "test-token" not in text and "fakehash" not in text


def test_a_telegram_failure_in_one_group_does_not_stop_the_others_or_move_its_position(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "a"), Group("-200", "b")],
                      messages={"-100": [msg("-100", 1, "x", 30, files=[("a.pdf", "pdf", 5)])], "-200": [msg("-200", 1, "y", 30)]},
                      fail_downloads=True)
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg, amide=FakeAmide(watch=["-100", "-200"]))
    poll(runner)
    assert [p.chat_id for p in amide.sent] == ["-200"] and state.chat("-100").last_id == 0


# ---------------------------------------------------------------- the gone check

def test_a_quiet_group_is_not_gone_and_one_bad_poll_is_not_enough(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], access={"-100": [Access("gone", "removed from the group"), Access("ok")]})
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg)
    poll(runner)
    assert amide.states == [] and state.chat("-100").strikes == 1
    poll(runner)
    assert amide.states == [] and state.chat("-100").strikes == 0


def test_two_polls_in_a_row_report_gone_once_and_recovery_reports_active(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], access={"-100": [Access("gone", "removed from the group")] * 3 + [Access("ok")]})
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg)
    for _ in range(3):
        poll(runner)
    assert amide.states == [("-100", "gone", "removed from the group")]
    poll(runner)
    assert amide.states[-1] == ("-100", "active", None) and state.chat("-100").gone is False


def test_an_unknown_answer_such_as_a_network_error_changes_nothing(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], access={"-100": [Access("gone", "x"), Access("unknown"), Access("unknown"), Access("gone", "x")]})
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg)
    for _ in range(4):
        poll(runner)
    assert state.chat("-100").strikes == 2 and len(amide.states) == 1                       # gone, unknown, unknown, gone: reported on the second strike


def test_a_gone_report_that_could_not_be_delivered_is_retried_next_poll(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], access={"-100": [Access("gone", "x")] * 4})
    amide = FakeAmide(watch=["-100"], state_ok=[False, True])
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg, amide=amide)
    for _ in range(3):
        poll(runner)
    assert state.chat("-100").gone is True and len(amide.states) == 2


def test_pauses_between_groups_are_taken_so_telegram_is_not_hammered(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "a"), Group("-200", "b")], messages={})
    runner, tg, amide, state, queue, slept = build(tmp_path, tg=tg, amide=FakeAmide(watch=["-100", "-200"]))
    poll(runner)
    assert slept and all(s > 0 for s in slept)
