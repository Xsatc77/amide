from datetime import date, timedelta

from sqlalchemy import select

from app.models import FitnessTestExerciseName, FitnessTestResult
from app.routers.fitness_test import fitness_test_logged_dates


def test_log_a_fitness_test_result(client, db):
    r = client.post("/fitness-test", data={
        "tested_at": "2026-01-08",
        "max_pushups": "20",
        "max_situps": "15",
    }, follow_redirects=False)
    assert r.status_code == 303
    results = db.scalars(select(FitnessTestResult)).all()
    by_exercise = {r.exercise: r.value for r in results}
    assert by_exercise[FitnessTestExerciseName.MAX_PUSHUPS] == 20.0
    assert by_exercise[FitnessTestExerciseName.MAX_SITUPS] == 15.0
    assert FitnessTestExerciseName.PLANK_HOLD_SECONDS not in by_exercise  # left blank, not required


def test_retest_suggestion_appears_per_exercise_independently(client, db):
    db.add(FitnessTestResult(owner_id=1, exercise=FitnessTestExerciseName.MAX_PUSHUPS,
                             value=20, tested_at=date.today() - timedelta(days=30)))
    db.add(FitnessTestResult(owner_id=1, exercise=FitnessTestExerciseName.MAX_SITUPS,
                             value=15, tested_at=date.today() - timedelta(days=5)))
    db.commit()
    body = client.get("/fitness-test").text
    assert "retest" in body.lower()
    # crude but effective: the suggestion text must be near Push-ups, not Sit-ups
    pushup_idx = body.lower().index("push")
    situp_idx = body.lower().index("sit")
    retest_idx = body.lower().index("retest")
    assert abs(retest_idx - pushup_idx) < abs(retest_idx - situp_idx)


def test_chart_renders_with_multiple_points(client, db):
    for i, value in enumerate([10, 15, 20]):
        db.add(FitnessTestResult(owner_id=1, exercise=FitnessTestExerciseName.MAX_PUSHUPS,
                                 value=value, tested_at=date.today() - timedelta(days=(2 - i) * 7)))
    db.commit()
    body = client.get("/fitness-test").text
    assert "<polyline" in body


import pytest  # noqa: E402


@pytest.mark.parametrize("form", [
    {"max_pushups": "20"},                                   # tested_at missing
    {"tested_at": "not-a-date", "max_pushups": "20"},
    {"tested_at": "2026-01-08", "max_pushups": "twenty"},
    {"tested_at": "2026-01-08", "max_pushups": "nan"},
    {"tested_at": "2026-01-08", "max_pushups": "inf"},
    {"tested_at": "2026-01-08", "max_pushups": "20", "max_situps": "-inf"},
])
def test_malformed_fitness_test_input_is_a_422_and_saves_nothing(client, db, form):
    r = client.post("/fitness-test", data=form, follow_redirects=False)
    assert r.status_code == 422
    assert db.scalars(select(FitnessTestResult)).all() == []


def test_fitness_test_logged_dates_only_returns_dates_actually_logged(db):
    db.add(FitnessTestResult(owner_id=1, exercise=FitnessTestExerciseName.MAX_PUSHUPS,
                             value=20, tested_at=date(2026, 1, 8)))
    db.add(FitnessTestResult(owner_id=1, exercise=FitnessTestExerciseName.MAX_SITUPS,
                             value=15, tested_at=date(2026, 1, 20)))  # outside the queried range
    db.commit()
    dates = fitness_test_logged_dates(db, 1, date(2026, 1, 1), date(2026, 1, 10))
    assert dates == {date(2026, 1, 8)}
