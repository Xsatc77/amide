"""Water for a day you forgot to log, and changing an entry's amount or day."""

from datetime import timedelta

import pytest

from app import clock
from app.db import SessionLocal
from app.models import User, WaterLog

TODAY = clock.today()


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(WaterLog).delete()
    db.get(User, me).water_goal_oz = None
    db.commit()


def day(n):
    return (TODAY - timedelta(days=n)).isoformat()


def entries():
    with SessionLocal() as s:
        return sorted((w.logged_at, w.ounces) for w in s.query(WaterLog))


def test_water_can_be_added_to_a_past_day_and_counts_in_the_history(client, db, me):
    db.get(User, me).water_goal_oz = 100
    db.commit()
    for n in (1, 2):
        assert client.post("/water/add", data={"day": day(n), "ounces": "96"}, follow_redirects=False).status_code == 303
    assert entries() == [(TODAY - timedelta(days=2), 96), (TODAY - timedelta(days=1), 96)]
    page = client.get("/measurements?tab=food").text
    assert 'action="/water/add"' in page and "Forgot To Log Water?" in page and "Compliant" in page


def test_a_future_day_a_blank_amount_or_a_day_over_a_year_back_is_ignored(client, me):
    for data in ({"day": (TODAY + timedelta(days=1)).isoformat(), "ounces": "8"}, {"day": day(1), "ounces": ""}, {"day": day(1), "ounces": "-4"},
                 {"day": day(400), "ounces": "8"}, {"day": "nonsense", "ounces": "8"}):
        client.post("/water/add", data=data)
    assert entries() == []


def test_an_entry_can_be_changed_to_another_amount_and_day(client, db, me):
    db.add(WaterLog(owner_id=me, logged_at=TODAY, ounces=8, goal_oz=100))
    db.commit()
    wid = db.query(WaterLog).one().id
    assert 'action="/water/%d/edit"' % wid in client.get("/measurements?tab=food").text
    client.post(f"/water/{wid}/edit", data={"day": day(3), "ounces": "24"})
    assert entries() == [(TODAY - timedelta(days=3), 24)]
    client.post(f"/water/{wid}/edit", data={"day": day(3), "ounces": "0"})                  # a bad value leaves it as it was
    assert entries() == [(TODAY - timedelta(days=3), 24)]
    assert client.post("/water/999999/edit", data={"day": day(1), "ounces": "8"}).status_code == 404
