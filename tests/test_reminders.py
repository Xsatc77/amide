"""Dose reminders sent as ntfy push messages (opt-in; the only outside call Amide makes on its own)."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import reminders
from app.db import SessionLocal
from app.models import (
    DoseLog, DoseReminder, DoseStatus, DoseUnit, Frequency, Peptide, PeptideSource, Protocol, ProtocolItem, Route, TimeOfDay, User,
)

DAY = date(2026, 10, 7)


class Recorder:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __call__(self, server, topic, title, message):
        if self.fail:
            raise OSError("down")
        self.sent.append((server, topic, title, message))


def at(hour, minute=0, tz="UTC"):
    """A UTC instant whose clock reads hour:minute in the zone `tz` on DAY."""
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


@pytest.fixture
def setup(me):
    ids = {}

    def build(slot=TimeOfDay.AM, *, enabled=True, topic="topic-abc-123", tz=None, name="Reminder Test Peptide"):
        with SessionLocal() as s:
            user = s.get(User, me)
            user.ntfy_enabled, user.ntfy_topic, user.timezone = enabled, topic, tz
            pep = Peptide(name=name, source=PeptideSource.CUSTOM)
            s.add(pep)
            s.flush()
            p = Protocol(name="Reminder Protocol", start_date=DAY - timedelta(days=3), owner_id=me)
            s.add(p)
            s.flush()
            item = ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=250, dose_unit=DoseUnit.MCG, frequency=Frequency.DAILY, time_of_day=slot, route=Route.SUBQ)
            s.add(item)
            s.commit()
            ids.update(protocol=p.id, item=item.id, pep=pep.id)
        return ids

    yield build
    with SessionLocal() as s:
        s.query(DoseReminder).delete()
        s.query(DoseLog).filter(DoseLog.peptide_name.like("Reminder%")).delete(synchronize_session=False)
        s.query(Protocol).filter(Protocol.name == "Reminder Protocol").delete(synchronize_session=False)
        s.query(Peptide).filter(Peptide.name.like("Reminder%")).delete(synchronize_session=False)
        user = s.get(User, me)
        user.ntfy_enabled, user.ntfy_topic, user.timezone = False, None, None
        s.commit()


def run(now, send):
    return reminders.run_once(SessionLocal, now, send=send)


def test_a_dose_is_reminded_once_when_its_time_of_day_arrives(setup):
    setup(TimeOfDay.AM)
    send = Recorder()
    assert run(at(7, 59), send) == 0 and send.sent == []                               # AM is 08:00: too early
    assert run(at(8, 5), send) == 1
    server, topic, title, message = send.sent[0]
    assert topic == "topic-abc-123" and "Reminder Test Peptide" in title and "250 mcg" in message and "AM" in message
    assert run(at(8, 30), send) == 0 and len(send.sent) == 1                           # never twice for the same dose and day


def test_nothing_is_sent_long_after_the_time(setup):
    setup(TimeOfDay.AM)
    send = Recorder()
    assert run(at(12, 0), send) == 0 and send.sent == []                               # past the three-hour window


def test_any_time_doses_get_no_reminder(setup):
    setup(TimeOfDay.ANY)
    send = Recorder()
    assert run(at(9, 0), send) == 0 and send.sent == []


def test_a_logged_dose_is_not_reminded(setup, me):
    ids = setup(TimeOfDay.AM)
    with SessionLocal() as s:
        s.add(DoseLog(owner_id=me, protocol_id=ids["protocol"], protocol_item_id=ids["item"], peptide_id=ids["pep"], peptide_name="Reminder Test Peptide", dose_value=250,
                      dose_unit=DoseUnit.MCG, route="subq", scheduled_date=DAY, scheduled_time_of_day=TimeOfDay.AM, status=DoseStatus.ON_TIME))
        s.commit()
    send = Recorder()
    assert run(at(8, 10), send) == 0


def test_the_users_own_timezone_decides_when_it_is_8am(setup):
    setup(TimeOfDay.AM, tz="America/Chicago")
    send = Recorder()
    assert run(at(8, 10, "UTC"), send) == 0                                            # 08:10 UTC is 03:10 in Chicago
    assert run(at(8, 10, "America/Chicago"), send) == 1


def test_reminders_off_or_without_a_topic_send_nothing(setup):
    setup(enabled=False)
    send = Recorder()
    assert run(at(8, 5), send) == 0
    setup(enabled=True, topic=None, name="Reminder No Topic")
    assert run(at(8, 5), send) == 0 and send.sent == []


def test_a_failed_send_is_tried_again_on_the_next_round(setup):
    setup(TimeOfDay.AM)
    assert run(at(8, 5), Recorder(fail=True)) == 0
    good = Recorder()
    assert run(at(8, 6), good) == 1 and len(good.sent) == 1


# ---------------------------------------------------------------- settings

def test_settings_turns_reminders_on_and_makes_a_topic(client, db, me, monkeypatch):
    sent = Recorder()
    monkeypatch.setattr(reminders, "send_ntfy", sent)
    assert "ntfy" in client.get("/settings").text
    r = client.post("/settings/reminders", data={"ntfy_enabled": "1", "ntfy_topic": ""}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        user = s.get(User, me)
        assert user.ntfy_enabled is True and user.ntfy_topic and len(user.ntfy_topic) >= 20
        topic = user.ntfy_topic
    assert topic in client.get("/settings").text
    client.post("/settings/reminders/test")
    assert sent.sent and sent.sent[0][1] == topic
    client.post("/settings/reminders", data={"ntfy_topic": topic})
    with SessionLocal() as s:
        assert s.get(User, me).ntfy_enabled is False
        s.get(User, me).ntfy_topic = None
        s.commit()


def test_a_bad_topic_is_refused(client, db):
    assert client.post("/settings/reminders", data={"ntfy_enabled": "1", "ntfy_topic": "has spaces!"}).status_code == 422
    assert client.post("/settings/reminders", data={"ntfy_enabled": "1", "ntfy_topic": "x"}).status_code == 422
