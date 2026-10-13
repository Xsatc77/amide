"""The derived charts on the Measurements page: listed in the Overview dropdown and again in the grid below it."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import units
from app.measurements import extra_charts
from app.models import (ActivityLevel, BiologicalSex, BodyMeasurement, FoodLog, User, WeightUnit, WorkoutExerciseLog, WorkoutLog)
from app.routers import measurements as router

TODAY = date.today()


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(BodyMeasurement).delete()
    db.query(FoodLog).delete()
    db.query(WorkoutLog).delete()
    user = db.get(User, me)
    user.sex = user.height_in = user.birth_date = user.activity_level = None
    db.commit()


def weigh(db, me, days_ago, weight, **tape):
    db.add(BodyMeasurement(owner_id=me, measured_at=TODAY - timedelta(days=days_ago), weight_lbs=weight, **tape))
    db.commit()


def series(db, me, window_start=None, u=None, markers=()):
    user = db.get(User, me)
    entries = db.scalars(select(BodyMeasurement).where(BodyMeasurement.owner_id == me).order_by(BodyMeasurement.measured_at)).all()
    out = extra_charts.extra_series(db, user, entries, window_start, u or units.Units(), router._chart, router._dual_chart, list(markers))
    return {s["key"]: s for s in out}


def values(chart):
    return [v for _, v in chart["raw"]]


def test_markers_are_placed_on_the_date_axis_and_only_inside_the_chart():
    chart = router._chart([(date(2026, 9, 1), 200.0), (date(2026, 9, 11), 190.0)])
    extra_charts.attach_markers(chart, [{"date": date(2026, 9, 1), "label": "A (start)"}, {"date": date(2026, 9, 6), "label": "A 2 mg"},
                                        {"date": date(2026, 8, 1), "label": "too early"}])
    xs = [m["x"] for m in chart["markers"]]
    assert [m["label"] for m in chart["markers"]] == ["A (start)", "A 2 mg"]
    assert xs[0] == chart["points"][0][0] and xs[1] == pytest.approx((chart["points"][0][0] + chart["points"][1][0]) / 2, abs=0.1)
    assert extra_charts.attach_markers(None, [{"date": date(2026, 9, 1), "label": "x"}]) is None


def test_the_weight_trend_and_weekly_change_come_from_the_weigh_ins(db, me):
    for days_ago, weight in ((21, 200.0), (14, 198.0), (7, 196.0), (0, 194.0)):
        weigh(db, me, days_ago, weight)
    s = series(db, me)
    assert values(s["weight_avg7"]["chart"]) == [200.0, 198.0, 196.0, 194.0]
    assert values(s["weight_rate"]["chart"]) == [-2.0, -2.0, -2.0]


def test_the_trend_for_a_window_still_uses_the_days_before_its_start(db, me):
    weigh(db, me, 10, 200.0)
    weigh(db, me, 4, 190.0)
    weigh(db, me, 1, 188.0)
    s = series(db, me, window_start=TODAY - timedelta(days=5))
    assert values(s["weight_avg7"]["chart"]) == [195.0, 189.0]     # the first point averages in day 10, which is before the window; the second no longer does


def test_metric_users_see_kilograms(db, me):
    weigh(db, me, 14, 220.0)
    weigh(db, me, 0, 220.0)
    s = series(db, me, u=units.Units("metric"))
    assert values(s["weight_avg7"]["chart"]) == [99.8, 99.8] and "kg" in s["weight_avg7"]["label"]


def test_fat_and_lean_mass_need_the_profile_and_tape_measurements(db, me):
    weigh(db, me, 3, 200.0, neck_in=16.0, waist_in=36.0)
    weigh(db, me, 0, 196.0, neck_in=16.0, waist_in=35.0)
    assert series(db, me)["fat_mass"]["chart"] is None                     # no height or sex yet
    user = db.get(User, me)
    user.sex, user.height_in = BiologicalSex.MALE, 70.0
    db.commit()
    s = series(db, me)
    fat, lean = values(s["fat_mass"]["chart"]), values(s["lean_mass"]["chart"])
    assert len(fat) == 2 and fat[1] < fat[0] and all(f + l == pytest.approx(w, abs=0.15) for f, l, w in zip(fat, lean, (200.0, 196.0)))


def test_waist_ratios(db, me):
    user = db.get(User, me)
    user.height_in = 70.0
    db.commit()
    weigh(db, me, 0, 200.0, waist_in=35.0, hips_in=40.0)
    weigh(db, me, 7, 202.0, waist_in=36.0)
    s = series(db, me)
    assert values(s["whtr"]["chart"]) == [0.514, 0.5] and values(s["whr"]["chart"]) == [0.875]


def test_cardio_minutes_and_weight_lifted_are_totalled_per_workout_day(db, me):
    day = TODAY - timedelta(days=2)
    log = WorkoutLog(owner_id=me, plan_day_id=None, day_label="Mixed", plan_name="P", log_date=day)
    log.exercise_logs += [
        WorkoutExerciseLog(name="Treadmill", completed=True, equipment="Conditioning/Cardio", duration_min=20.0),
        WorkoutExerciseLog(name="Bike", completed=True, equipment="Conditioning/Cardio", duration_min=15.5),
        WorkoutExerciseLog(name="Bench Press", completed=True, equipment="Barbell", weight_value=100.0, weight_unit=WeightUnit.LB, sets=3, reps_value=10, volume_lb=3000.0),
        WorkoutExerciseLog(name="Plank", completed=True, equipment="Bodyweight", duration_min=3.0),
        WorkoutExerciseLog(name="Skipped press", completed=False, equipment="Barbell", volume_lb=9999.0)]
    db.add(log)
    db.commit()
    s = series(db, me)
    assert s["cardio_min"]["chart"]["raw"] == [(day, 35.5)]                # the plank's minutes are not cardio; the unticked exercise is not counted
    assert s["lifted"]["chart"]["raw"] == [(day, 3000)]


def test_calories_against_the_deficit_has_a_line_for_each(db, me, monkeypatch):
    user = db.get(User, me)
    user.sex, user.birth_date, user.height_in, user.activity_level = BiologicalSex.MALE, date(1985, 1, 15), 70.0, ActivityLevel.MODERATELY_ACTIVE
    db.commit()
    weigh(db, me, 5, 190.0)
    for days_ago, kcal in ((2, 1800), (1, 2400)):
        db.add(FoodLog(owner_id=me, eaten_on=TODAY - timedelta(days=days_ago), meal="lunch", name="Rice", serving="1 cup", servings=1, calories=kcal,
                       protein_g=1, carb_g=1, fat_g=1, fiber_g=1))
    db.commit()
    from app.food import day_overview
    monkeypatch.setattr(day_overview, "burn_by_day", lambda session, uid: {TODAY - timedelta(days=2): 300.0})
    cal = series(db, me)["cal_deficit"]
    base = day_overview.maintenance(db, user)[0]
    assert cal["kind"] == "dual" and [v for _, v in cal["chart"]["raw_a"]] == [1800, 2400]
    assert [v for _, v in cal["chart"]["raw_b"]] == [round(base + 300 - 1800), round(base - 2400)]


def test_without_a_profile_calories_still_chart_and_the_deficit_line_is_empty(db, me):
    db.add(FoodLog(owner_id=me, eaten_on=TODAY, meal="lunch", name="Rice", serving="1 cup", servings=1, calories=1500, protein_g=1, carb_g=1, fat_g=1, fiber_g=1))
    db.commit()
    cal = series(db, me)["cal_deficit"]
    assert [v for _, v in cal["chart"]["raw_a"]] == [1500] and cal["chart"]["raw_b"] == []


def test_the_page_lists_each_chart_in_the_dropdown_and_the_grid(client, db, me):
    user = db.get(User, me)
    user.height_in = 70.0
    db.commit()
    for days_ago, weight in ((14, 200.0), (7, 198.0), (0, 196.0)):
        weigh(db, me, days_ago, weight, waist_in=35.0 + days_ago / 14)
    page = client.get("/measurements?range=1mo").text
    for key in ("weight_avg7", "weight_rate", "whtr"):
        assert f'<option value="{key}">' in page and f'data-metric="{key}"' in page
    assert page.count("Weight, 7-day average") >= 3 and page.count("Waist-to-height ratio") >= 3          # option + overview panel + grid
    grid = page.split('id="charts-heading"', 1)[1]
    assert "Weight, 7-day average" in grid and "Waist-to-height ratio" in grid
