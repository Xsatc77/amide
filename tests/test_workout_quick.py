"""Quick activity: a walk, run or machine session logged in one step (activity, minutes, distance, date), with calories from the encoded database."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BodyMeasurement, User, WorkoutExerciseLog, WorkoutLog
from app.workouts.quick import ACTIVITIES


@pytest.fixture(autouse=True)
def clean_rows():
    yield
    with SessionLocal() as s:
        s.query(WorkoutLog).delete()
        s.query(BodyMeasurement).delete()
        s.commit()


def tester():
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == "tester"))


def weigh_in(lb=200):
    with SessionLocal() as s:
        s.add(BodyMeasurement(owner_id=tester(), measured_at=datetime.combine(date.today(), datetime.min.time()), weight_lbs=lb))
        s.commit()


def logs():
    with SessionLocal() as s:
        return [(w, list(w.exercise_logs)) for w in s.scalars(select(WorkoutLog)).all()]


def test_the_activity_choices_are_the_cardio_the_database_can_estimate():
    assert {"walk", "jog", "run"} <= set(ACTIVITIES) and len(ACTIVITIES) >= 8


def test_a_walk_with_distance_gets_its_speed_and_a_calorie_estimate(client):
    weigh_in(200)
    r = client.post("/workouts/quick", data={"activity": "walk", "minutes": "30", "distance": "1.5", "log_date": date.today().isoformat()}, follow_redirects=False)
    assert r.status_code == 303 and "journal" in r.headers["location"], r.text
    [(log, rows)] = logs()
    assert log.day_label == "Walk" and log.plan_day_id is None and log.log_date == date.today()
    [row] = rows
    assert row.completed and row.duration_min == 30 and row.speed_mph == pytest.approx(3.0)
    assert row.db_exercise == "Treadmill Walk" and row.net_kcal and row.net_kcal > 0


def test_a_machine_session_needs_no_distance_and_no_weight_means_no_estimate_but_still_saves(client):
    r = client.post("/workouts/quick", data={"activity": "elliptical", "minutes": "20", "distance": "", "log_date": date.today().isoformat()}, follow_redirects=False)
    assert r.status_code == 303, r.text
    [(log, [row])] = logs()
    assert log.day_label == "Elliptical" and row.duration_min == 20 and row.speed_mph is None


@pytest.mark.parametrize("data", [
    {"activity": "swim", "minutes": "30"},                      # not in the database
    {"activity": "walk", "minutes": ""},
    {"activity": "walk", "minutes": "0"},
    {"activity": "walk", "minutes": "5000"},
    {"activity": "walk", "minutes": "30", "distance": "-2"},
    {"activity": "walk", "minutes": "30", "log_date": "not-a-date"},
    {"activity": "walk", "minutes": "30", "log_date": (date.today() + timedelta(days=30)).isoformat()},
])
def test_bad_input_is_a_422_and_saves_nothing(client, data):
    assert client.post("/workouts/quick", data=data, follow_redirects=False).status_code == 422
    assert logs() == []


def test_the_quick_dialog_is_on_the_workouts_page_and_the_journal(client):
    for path in ("/workouts", "/measurements?tab=journal"):
        page = client.get(path).text
        assert 'action="/workouts/quick"' in page and 'name="minutes"' in page and 'name="distance"' in page


def test_last_time_for_an_exercise_added_on_the_day(client):
    from app.models import WorkoutExerciseLog
    day = date.today() - timedelta(days=5)
    with SessionLocal() as s:
        log = WorkoutLog(owner_id=tester(), day_label="Lasttime Day", log_date=day)
        log.exercise_logs = [WorkoutExerciseLog(name="Bench Press", db_exercise="Bench Press", completed=True,
                                                weight_value=135, weight_unit=__import__("app.models", fromlist=["WeightUnit"]).WeightUnit.LB, sets=3, reps_value=8)]
        s.add(log)
        s.commit()
    found = client.get("/workouts/last", params={"name": "bench press"}).json()
    assert found["last"]["text"] == "135 lb · 3 × 8" and found["last"]["date"] == day.isoformat() and found["name"] == "Bench Press"
    assert client.get("/workouts/last", params={"name": "Not A Real Exercise Xyz"}).json()["last"] is None
    assert client.get("/workouts/last", params={"name": ""}).json()["last"] is None


def test_the_log_page_keeps_the_screen_awake_and_shows_last_time_for_added_exercises(client):
    from pathlib import Path
    page = client.get("/workouts/free/log").text
    assert "keep-awake.js" in page and "data-keep-awake-note" in page and "workout-log.js" in page
    awake = (Path(__file__).parent.parent / "app" / "static" / "js" / "keep-awake.js").read_text(encoding="utf-8")
    assert "wakeLock.request" in awake and "visibilitychange" in awake
    log_js = (Path(__file__).parent.parent / "app" / "static" / "js" / "workout-log.js").read_text(encoding="utf-8")
    assert "/workouts/last" in log_js and "Last time" in log_js
