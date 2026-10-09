"""Deleting your own body measurements, journal entries and water entries, each behind a confirmation and never anyone else's."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BodyMeasurement, JournalEntry, JournalEntrySideEffect, JournalSideEffect, User, WaterLog
from photo_helpers import other_client


def tester():
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == "tester"))


@pytest.fixture(autouse=True)
def clean_rows():
    yield
    with SessionLocal() as s:
        for model in (BodyMeasurement, JournalEntry, WaterLog):
            s.query(model).delete()
        s.commit()


def test_a_measurement_can_be_deleted_with_a_confirmation_and_others_cannot(client):
    with SessionLocal() as s:
        mine = BodyMeasurement(owner_id=tester(), measured_at=datetime(2026, 9, 1, 8, 0), weight_lbs=200)
        s.add(mine)
        s.commit()
        mid = mine.id
    page = client.get("/measurements?tab=measurements").text
    assert f'action="/measurements/{mid}/delete"' in page and "data-confirm" in page
    with other_client("delother1") as stranger:
        assert stranger.post(f"/measurements/{mid}/delete", follow_redirects=False).status_code == 404
    with SessionLocal() as s:
        assert s.get(BodyMeasurement, mid) is not None
    r = client.post(f"/measurements/{mid}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/measurements")
    with SessionLocal() as s:
        assert s.get(BodyMeasurement, mid) is None


def test_a_journal_entry_and_its_side_effects_are_deleted_together(client):
    day = date(2026, 9, 2)
    with SessionLocal() as s:
        entry = JournalEntry(owner_id=tester(), entry_date=day, mood=3, notes="rough day")
        entry.side_effects.append(JournalEntrySideEffect(side_effect=JournalSideEffect.NAUSEA))
        s.add(entry)
        s.commit()
        eid = entry.id
    page = client.get("/measurements?tab=journal").text
    assert f'action="/journal/entries/{day.isoformat()}/delete"' in page and "data-confirm" in page
    with other_client("delother2") as stranger:
        assert stranger.post(f"/journal/entries/{day.isoformat()}/delete", follow_redirects=False).status_code == 404
    with SessionLocal() as s:
        assert s.get(JournalEntry, eid) is not None
    assert client.post(f"/journal/entries/{day.isoformat()}/delete", follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        assert s.get(JournalEntry, eid) is None
        assert s.scalar(select(JournalEntrySideEffect).where(JournalEntrySideEffect.entry_id == eid)) is None


def test_water_entries_are_listed_and_can_be_deleted_one_at_a_time(client):
    today = date.today()
    with SessionLocal() as s:
        a = WaterLog(owner_id=tester(), logged_at=today, ounces=16)
        b = WaterLog(owner_id=tester(), logged_at=today - timedelta(days=1), ounces=8)
        s.add_all([a, b])
        s.commit()
        aid, bid = a.id, b.id
    page = client.get("/measurements?tab=food").text
    assert f'action="/water/{aid}/delete"' in page and f'action="/water/{bid}/delete"' in page
    with other_client("delother3") as stranger:
        assert stranger.post(f"/water/{aid}/delete", follow_redirects=False).status_code == 404
    assert client.post(f"/water/{aid}/delete", follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        assert s.get(WaterLog, aid) is None and s.get(WaterLog, bid) is not None


def test_deleting_something_that_is_already_gone_is_a_404_not_an_error(client):
    assert client.post("/measurements/999999/delete", follow_redirects=False).status_code == 404
    assert client.post("/journal/entries/2001-01-01/delete", follow_redirects=False).status_code == 404
    assert client.post("/water/999999/delete", follow_redirects=False).status_code == 404
