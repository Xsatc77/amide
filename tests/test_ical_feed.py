"""A private calendar feed (iCal) that Apple, Google and Outlook calendars can subscribe to."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.calendar.ical import build_calendar, fold, escape
from app.db import SessionLocal
from app.main import app
from app.models import DoseUnit, Frequency, Peptide, PeptideSource, Protocol, ProtocolItem, Route, TimeOfDay, User

TODAY = date.today()


def test_text_is_escaped_and_long_lines_are_folded():
    bs = chr(92)
    assert escape("a,b;c" + chr(10) + "d" + bs + "e") == "a" + bs + ",b" + bs + ";c" + bs + "nd" + bs * 2 + "e"
    folded = fold("X:" + "y" * 200)
    assert all(len(line.encode()) <= 75 for line in folded.split("\r\n")) and folded.replace("\r\n ", "") == "X:" + "y" * 200


def test_the_calendar_has_a_timed_event_per_dose_with_an_alarm_and_an_all_day_one_for_any_time():
    from types import SimpleNamespace
    item = lambda tod, n: SimpleNamespace(peptide="Zorvex", dose=250, unit="mcg", step=None, time_of_day=tod, route="SubQ", protocol_item_id=n)
    occ = SimpleNamespace(date=date(2026, 10, 9), protocol_id=3, protocol_name="Spring, cut", items=[item(TimeOfDay.AM, 1), item(TimeOfDay.ANY, 2)])
    text = build_calendar([occ])
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n") and text.count("BEGIN:VEVENT") == 2
    assert "UID:amide-1-20261009@amide" in text and "DTSTART:20261009T080000" in text and "SUMMARY:Zorvex 250 mcg (AM)" in text
    assert "DTSTART;VALUE=DATE:20261009" in text and "BEGIN:VALARM" in text and "Spring\, cut" in text


@pytest.fixture
def feed(me):
    with SessionLocal() as s:
        pep = Peptide(name="Feed Test Peptide", source=PeptideSource.CUSTOM)
        s.add(pep)
        s.flush()
        p = Protocol(name="Feed Protocol", start_date=TODAY - timedelta(days=2), owner_id=me)
        s.add(p)
        s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=1, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, time_of_day=TimeOfDay.PM, route=Route.SUBQ))
        user = s.get(User, me)
        user.calendar_token = None
        s.commit()
        ids = (p.id, pep.id)
    yield ids
    with SessionLocal() as s:
        s.get(User, me).calendar_token = None
        s.query(Protocol).filter_by(id=ids[0]).delete()
        s.query(Peptide).filter_by(id=ids[1]).delete()
        s.commit()


def test_settings_makes_a_link_that_works_without_signing_in(client, db, me, feed):
    assert "calendar/feed" not in client.get("/settings").text
    r = client.post("/settings/calendar-feed", data={"action": "create"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        token = s.get(User, me).calendar_token
    assert token and len(token) >= 32
    assert f"/calendar/feed/{token}.ics" in client.get("/settings").text
    with TestClient(app) as anon:
        r = anon.get(f"/calendar/feed/{token}.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar") and "no-store" in r.headers["cache-control"]
    assert "SUMMARY:Feed Test Peptide 1 mg (PM)" in r.text and r.text.count("BEGIN:VEVENT") >= 60                       # a window of daily doses


def test_a_new_link_replaces_the_old_and_turning_it_off_closes_it(client, db, me, feed):
    client.post("/settings/calendar-feed", data={"action": "create"})
    with SessionLocal() as s:
        first = s.get(User, me).calendar_token
    client.post("/settings/calendar-feed", data={"action": "create"})
    with SessionLocal() as s:
        second = s.get(User, me).calendar_token
    assert first != second
    with TestClient(app) as anon:
        assert anon.get(f"/calendar/feed/{first}.ics").status_code == 404
        assert anon.get(f"/calendar/feed/{second}.ics").status_code == 200
        client.post("/settings/calendar-feed", data={"action": "off"})
        assert anon.get(f"/calendar/feed/{second}.ics").status_code == 404
    with SessionLocal() as s:
        assert s.get(User, me).calendar_token is None


def test_a_wrong_or_empty_token_finds_nothing(client, db):
    with TestClient(app) as anon:
        assert anon.get("/calendar/feed/not-a-real-token.ics").status_code == 404
        assert anon.get("/calendar/feed/.ics").status_code == 404
