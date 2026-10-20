"""Each of the last 14 days' water against that day's goal, with the 90% rule the H2O compliance bar uses."""

from datetime import timedelta

import pytest

from app import clock, water
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


def add(db, me, days_ago, oz, goal=100):
    db.add(WaterLog(owner_id=me, logged_at=TODAY - timedelta(days=days_ago), ounces=oz, goal_oz=goal))
    db.commit()


def rows(db, me):
    return water.history(db, db.get(User, me), TODAY)


def test_it_lists_the_last_fourteen_days_oldest_first_ending_today(db, me):
    db.get(User, me).water_goal_oz = 100
    db.commit()
    r = rows(db, me)
    assert len(r) == 14 and r[0].day == TODAY - timedelta(days=13) and r[-1].day == TODAY


def test_each_day_is_judged_against_its_own_goal_with_the_ninety_percent_rule(db, me):
    add(db, me, 5, 90, goal=100)              # exactly 90%: compliant
    add(db, me, 4, 89, goal=100)              # just under: short
    add(db, me, 3, 120, goal=130)             # a different goal that day: 92%
    add(db, me, 2, 60, goal=130)
    by = {r.day: r for r in rows(db, me)}
    assert (by[TODAY - timedelta(days=5)].status, by[TODAY - timedelta(days=5)].pct) == ("compliant", 90)
    assert by[TODAY - timedelta(days=4)].status == "short"
    assert (by[TODAY - timedelta(days=3)].status, by[TODAY - timedelta(days=3)].goal) == ("compliant", 130)
    assert by[TODAY - timedelta(days=2)].status == "short"


def test_a_day_with_nothing_logged_says_so_and_today_is_not_counted_yet(db, me):
    db.get(User, me).water_goal_oz = 100
    db.commit()
    add(db, me, 0, 40, goal=100)
    by = {r.day: r for r in rows(db, me)}
    assert by[TODAY - timedelta(days=1)].status == "none" and by[TODAY - timedelta(days=1)].oz == 0
    assert by[TODAY].status == "today" and by[TODAY].oz == 40


def test_the_summary_counts_only_finished_days(db, me):
    add(db, me, 3, 100)
    add(db, me, 2, 95)
    add(db, me, 1, 50)
    add(db, me, 0, 100)                       # today: not counted
    summary = water.history_summary(rows(db, me))
    assert summary == {"compliant": 2, "days": 13}


def test_the_food_tab_shows_the_table_with_a_summary(client, db, me):
    from app.models import BodyMeasurement
    from datetime import datetime
    db.add(BodyMeasurement(owner_id=me, measured_at=datetime(2026, 9, 1), weight_lbs=200))
    db.commit()
    add(db, me, 2, 95, goal=100)
    try:
        page = client.get("/measurements?tab=food").text
        assert "last 14 days" in page and "1 of 13" in page and "Compliant" in page
    finally:
        db.query(BodyMeasurement).delete()
        db.commit()


def test_the_percentage_never_rounds_up_into_a_compliance_it_did_not_earn(db, me):
    add(db, me, 3, 89.6, goal=100)            # 89.6% is short: it must not read "90%" next to "Short of 90%"
    day = TODAY - timedelta(days=3)
    row = next(r for r in rows(db, me) if r.day == day)
    assert row.status == "short" and row.pct == 89


def test_the_table_says_when_no_time_zone_is_set_because_days_then_follow_the_server(client, db, me):
    from app.models import BodyMeasurement
    from datetime import datetime
    db.add(BodyMeasurement(owner_id=me, measured_at=datetime(2026, 9, 1), weight_lbs=200))
    db.get(User, me).timezone = None
    db.commit()
    try:
        assert "time zone" in client.get("/measurements?tab=food").text.split('id="water-history-heading"', 1)[1][:1500].lower()
        db.get(User, me).timezone = "America/Chicago"
        db.commit()
        assert "no time zone is set" not in client.get("/measurements?tab=food").text.lower()
    finally:
        db.get(User, me).timezone = None
        db.query(BodyMeasurement).delete()
        db.commit()
