"""A day's water is judged against the goal it had that day. Changing the goal later does not rewrite the past, and the Journal shows each day's intake with that day's goal."""

from datetime import date, timedelta

import pytest

from app import compliance as comp
from app.db import SessionLocal
from app.food import day_overview
from app.models import User, WaterLog

TODAY = date.today()


@pytest.fixture(autouse=True)
def clean(client, db, me):
    def wipe():
        db.rollback()
        db.query(WaterLog).delete()
        db.get(User, me).water_goal_oz = None
        db.commit()
    wipe()
    yield
    wipe()


def set_goal(db, me, oz):
    db.get(User, me).water_goal_oz = oz
    db.commit()


def add(db, me, days_ago, oz, goal):
    db.add(WaterLog(owner_id=me, logged_at=TODAY - timedelta(days=days_ago), ounces=oz, goal_oz=goal))
    db.commit()


def h2o(me, window=30):
    with SessionLocal() as s:
        return next(b for b in comp.bars(s, s.get(User, me), TODAY, window) if b["key"] == "h2o")


def test_logging_water_records_the_goal_it_was_logged_against(client, db, me):
    set_goal(db, me, 100)
    client.post("/dashboard/water/log", data={"ounces": "16"})
    row = db.query(WaterLog).one()
    assert row.goal_oz == 100


def test_a_day_is_judged_against_the_goal_it_had_even_after_the_goal_changes(db, me):
    add(db, me, 2, 95, goal=100)         # 95% of 100: passes
    add(db, me, 1, 95, goal=100)
    set_goal(db, me, 200)                 # the goal doubles afterwards
    bar = h2o(me)
    assert (bar["good"], bar["total"]) == (2, 2)


def test_a_lowered_goal_does_not_rescue_an_old_short_day(db, me):
    add(db, me, 2, 60, goal=100)          # fell short that day
    add(db, me, 1, 100, goal=100)
    set_goal(db, me, 50)                  # a much lower goal later
    bar = h2o(me)
    assert (bar["good"], bar["total"]) == (1, 2)


def test_water_logged_before_the_goal_was_recorded_uses_the_current_goal(db, me):
    set_goal(db, me, 100)
    add(db, me, 2, 95, goal=None)
    assert h2o(me)["good"] == 1


def test_the_last_goal_logged_on_a_day_is_that_days_goal(db, me):
    add(db, me, 2, 50, goal=100)
    add(db, me, 2, 50, goal=120)          # goal raised mid-day: 100 oz total is under 90% of 120
    assert h2o(me)["good"] == 0


def test_the_journal_day_row_carries_that_days_intake_and_goal(db, me):
    add(db, me, 2, 40, goal=100)
    add(db, me, 2, 30, goal=100)
    set_goal(db, me, 150)
    day = TODAY - timedelta(days=2)
    row = day_overview.day_overviews(db, db.get(User, me), [day])[day]
    assert row["water_oz"] == 70 and row["water_goal_oz"] == 100


def test_the_journal_shows_the_day_s_goal_next_to_the_intake(client, db, me):
    from app.models import JournalEntry
    day = TODAY - timedelta(days=2)
    db.add(JournalEntry(owner_id=me, entry_date=day))
    add(db, me, 2, 70, goal=100)
    set_goal(db, me, 150)
    page = client.get("/measurements?tab=journal").text
    db.query(JournalEntry).delete()
    db.commit()
    assert "of 100 oz" in page and "of 150 oz" not in page
