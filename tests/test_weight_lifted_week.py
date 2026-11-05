"""The Workouts This Week card on the dashboard: weight lifted Monday to Sunday in large numbers, with the average per workout."""

import re
from datetime import timedelta

import pytest

from app import clock
from app.models import User, WorkoutExerciseLog, WorkoutLog

TODAY = clock.today()
MONDAY = TODAY - timedelta(days=TODAY.weekday())


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(WorkoutLog).filter(WorkoutLog.owner_id == me).delete(synchronize_session=False)
    db.get(User, me).units = "us"
    db.commit()


def lift(db, me, day, *volumes):
    log = WorkoutLog(owner_id=me, log_date=day)
    db.add(log)
    db.flush()
    for i, v in enumerate(volumes):
        db.add(WorkoutExerciseLog(workout_log_id=log.id, name=f"Lift {i}", volume_lb=v))
    db.commit()


def card(page):
    found = re.search(r'<div class="workout-lifted".*?</div>', page, re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", found.group(0)).split()) if found else None


def test_the_week_total_and_the_average_per_workout_day(client, db, me):
    lift(db, me, MONDAY, 4000, 2500.4)
    lift(db, me, MONDAY + timedelta(days=1), 5500)
    lift(db, me, MONDAY - timedelta(days=1), 99999)              # last Sunday is not this week
    text = card(client.get("/dashboard").text)
    assert text.startswith("12,000 lb lifted this week, Monday to Sunday")
    assert "Average 6,000 lb per workout (2 days)" in text


def test_nothing_lifted_says_zero(client, db, me):
    assert card(client.get("/dashboard").text).startswith("0 lb lifted this week")


def test_it_is_shown_in_kilograms_for_a_metric_user(client, db, me):
    db.get(User, me).units = "metric"
    db.commit()
    lift(db, me, MONDAY, 2204.6)
    assert card(client.get("/dashboard").text).startswith("1,000 kg lifted this week")


def test_a_huge_total_has_its_own_wrapping_rules_so_it_stays_in_the_card():
    css = open("app/static/css/app.css", encoding="utf-8").read()
    block = css[css.index(".workout-lifted-total {"):]
    assert "flex-wrap: wrap" in block.split("}")[0] and "overflow-wrap: anywhere" in css[css.index(".workout-lifted p"):].split("}")[0]
