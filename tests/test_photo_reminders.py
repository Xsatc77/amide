"""Reminders to take body photos: two weeks after the last one was uploaded, and on every protocol's start and end date."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import photo_reminders as pr
from app import reminders
from app.db import SessionLocal
from app.models import BodyPhoto, PhotoReminderSent, Protocol, User

DAY = date(2026, 10, 7)


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(PhotoReminderSent).delete()
    db.query(BodyPhoto).delete()
    db.query(Protocol).filter(Protocol.name.like("Photoremind%")).delete(synchronize_session=False)
    user = db.get(User, me)
    user.ntfy_enabled, user.ntfy_topic, user.timezone = False, None, None
    db.commit()


def photo(db, me, uploaded: date, taken: date | None = None):
    db.add(BodyPhoto(owner_id=me, taken_on=taken or uploaded, filename="0" * 32 + ".jpg", created_at=datetime(uploaded.year, uploaded.month, uploaded.day, 12, 0, tzinfo=timezone.utc)))
    db.commit()


def protocol(db, me, start, end=None, name="Photoremind A"):
    db.add(Protocol(name=name, owner_id=me, start_date=start, end_date=end))
    db.commit()


def active(db, me, today):
    return pr.active(db, me, today)


def kinds(items):
    return sorted(r.kind for r in items)


# ---- two weeks after the last photo ----------------------------------------------------------------------------------------------------------

def test_there_is_no_two_week_reminder_until_a_photo_has_been_uploaded(db, me):
    assert active(db, me, DAY) == []
    assert pr.next_interval_due(db, me) is None


def test_the_reminder_falls_due_two_weeks_after_the_upload_and_stays_until_a_new_photo(db, me):
    photo(db, me, DAY)
    assert pr.next_interval_due(db, me) == DAY + timedelta(days=14)
    assert active(db, me, DAY + timedelta(days=13)) == []
    assert kinds(active(db, me, DAY + timedelta(days=14))) == ["interval"]
    assert kinds(active(db, me, DAY + timedelta(days=30))) == ["interval"]                # overdue: still asking


def test_a_new_photo_replaces_the_old_reminder_with_one_two_weeks_from_the_new_upload(db, me):
    photo(db, me, DAY)
    photo(db, me, DAY + timedelta(days=10))
    assert pr.next_interval_due(db, me) == DAY + timedelta(days=24)
    assert active(db, me, DAY + timedelta(days=14)) == []                                  # the first one's date: nothing any more
    assert kinds(active(db, me, DAY + timedelta(days=24))) == ["interval"]


def test_it_counts_from_the_upload_date_not_the_date_the_photo_was_taken(db, me):
    photo(db, me, DAY, taken=DAY - timedelta(days=60))
    assert pr.next_interval_due(db, me) == DAY + timedelta(days=14)


def test_the_upload_date_is_the_persons_own_calendar_day(db, me):
    user = db.get(User, me)
    user.timezone = "Pacific/Auckland"                                                    # 12:00 UTC on the 7th is 01:00 on the 8th there
    db.commit()
    photo(db, me, DAY)
    assert pr.next_interval_due(db, me) == DAY + timedelta(days=15)


# ---- protocol start and end ---------------------------------------------------------------------------------------------------------------

def test_a_protocols_start_date_asks_for_photos_that_day_and_for_a_few_days_after(db, me):
    protocol(db, me, DAY)
    assert active(db, me, DAY - timedelta(days=1)) == []
    r = active(db, me, DAY)
    assert kinds(r) == ["start"] and "Photoremind A" in r[0].text and "starts" in r[0].text
    assert kinds(active(db, me, DAY + timedelta(days=3))) == ["start"]
    assert active(db, me, DAY + timedelta(days=4)) == []                                   # stale: it stops asking


def test_a_protocols_end_date_asks_too_and_an_open_ended_protocol_has_no_end_reminder(db, me):
    protocol(db, me, DAY - timedelta(days=60), end=DAY)
    r = active(db, me, DAY)
    assert kinds(r) == ["end"] and "ends" in r[0].text
    protocol(db, me, DAY - timedelta(days=90), end=None, name="Photoremind B")
    assert kinds(active(db, me, DAY)) == ["end"]


def test_a_photo_taken_for_the_milestone_satisfies_it(db, me):
    protocol(db, me, DAY)
    photo(db, me, DAY, taken=DAY)
    assert active(db, me, DAY) == []
    protocol(db, me, DAY + timedelta(days=20), name="Photoremind B")
    assert active(db, me, DAY + timedelta(days=20)) != []                                  # an earlier photo does not cover a later start


def test_a_photo_taken_the_day_before_counts_for_the_start(db, me):
    protocol(db, me, DAY)
    photo(db, me, DAY - timedelta(days=1), taken=DAY - timedelta(days=1))
    assert active(db, me, DAY) == []


def test_another_persons_protocols_and_photos_are_not_mine(client, db, me):
    other = User(username="photoremother", username_key="photoremother", password_hash="x")
    db.add(other)
    db.commit()
    try:
        db.add(Protocol(name="Photoremind Other", owner_id=other.id, start_date=DAY))
        db.commit()
        assert active(db, me, DAY) == []
    finally:
        db.query(Protocol).filter(Protocol.owner_id == other.id).delete()
        db.delete(other)
        db.commit()


# ---- the calendar -------------------------------------------------------------------------------------------------------------------------

def test_calendar_dates_mark_start_end_and_the_two_week_day(db, me):
    protocol(db, me, DAY, end=DAY + timedelta(days=40))
    photo(db, me, DAY - timedelta(days=5))
    marks = pr.calendar_dates(db, me, DAY - timedelta(days=30), DAY + timedelta(days=60))
    assert set(marks) == {DAY, DAY + timedelta(days=40), DAY - timedelta(days=5) + timedelta(days=14)}
    assert "start" in marks[DAY][0].lower() and "end" in marks[DAY + timedelta(days=40)][0].lower()


def test_the_calendar_page_shows_a_camera_on_those_days(client, db, me):
    from app import clock
    today = clock.today()
    protocol(db, me, today)
    page = client.get("/calendar?view=month").text
    assert "📷" in page and "Photoremind A" in page


# ---- the dashboard ------------------------------------------------------------------------------------------------------------------------

def test_the_dashboard_asks_for_photos_when_one_is_due_and_links_to_the_upload(client, db, me):
    from app import clock
    protocol(db, me, clock.today())
    page = client.get("/dashboard").text
    assert "body photo" in page.lower() and "Photoremind A" in page and "add_photo=1" in page


def test_the_dashboard_is_quiet_when_nothing_is_due(client, db, me):
    assert "take your body photos" not in client.get("/dashboard").text.lower()


# ---- the push -----------------------------------------------------------------------------------------------------------------------------

class Recorder:
    def __init__(self):
        self.sent = []

    def __call__(self, server, topic, title, message):
        self.sent.append((topic, title, message))


def at(hour, tz="UTC", day=DAY):
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def enable(db, me, tz=None):
    user = db.get(User, me)
    user.ntfy_enabled, user.ntfy_topic, user.timezone = True, "photo-topic-123", tz
    db.commit()


def test_the_push_goes_out_once_in_the_morning_of_the_day_it_is_due(db, me):
    enable(db, me)
    protocol(db, me, DAY)
    send = Recorder()
    assert reminders.run_once(SessionLocal, at(8), send=send) == 0                          # before 09:00
    assert reminders.run_once(SessionLocal, at(9), send=send) == 1
    topic, title, message = send.sent[0]
    assert topic == "photo-topic-123" and "photo" in title.lower() and "Photoremind A" in message
    assert reminders.run_once(SessionLocal, at(10), send=send) == 0 and len(send.sent) == 1


def test_no_push_when_reminders_are_off_or_the_photo_is_already_taken(db, me):
    protocol(db, me, DAY)
    send = Recorder()
    assert reminders.run_once(SessionLocal, at(9), send=send) == 0                          # not opted in
    enable(db, me)
    photo(db, me, DAY, taken=DAY)
    assert reminders.run_once(SessionLocal, at(9), send=send) == 0 and send.sent == []


def test_an_overdue_two_week_reminder_is_pushed_once_a_day(db, me):
    enable(db, me)
    photo(db, me, DAY - timedelta(days=20))
    send = Recorder()
    assert reminders.run_once(SessionLocal, at(9), send=send) == 1
    assert reminders.run_once(SessionLocal, at(9, day=DAY + timedelta(days=1)), send=send) == 1      # the next morning it asks again


def test_an_ended_protocol_asks_for_no_photos(db, me):
    protocol(db, me, DAY, DAY + timedelta(days=30))
    db.query(Protocol).filter(Protocol.name == "Photoremind A").update({Protocol.ended_on: DAY})
    db.commit()
    assert active(db, me, DAY) == []
    assert active(db, me, DAY + timedelta(days=30)) == []


def test_a_paused_protocol_asks_for_no_photos(db, me):
    protocol(db, me, DAY)
    db.query(Protocol).filter(Protocol.name == "Photoremind A").update({Protocol.paused: True})
    db.commit()
    assert active(db, me, DAY) == []
