from datetime import date

import pytest
from sqlalchemy import select

from app.models import BodyMeasurement, WorkoutExerciseLog, WorkoutLog, WorkoutPlan
from app.workouts import calories, exercise_db
from test_workouts import _plan_form

DAY = "2026-02-02"


@pytest.fixture
def weigh_in(db, me):
    """The test user's only weigh-in: 200 lb. Other tests leave body measurements behind, so start from none; remove after."""
    db.query(BodyMeasurement).filter(BodyMeasurement.owner_id == me).delete()
    entry = BodyMeasurement(owner_id=me, measured_at=date(2026, 1, 1), weight_lbs=200.0)
    db.add(entry)
    db.commit()
    yield entry
    db.query(BodyMeasurement).filter(BodyMeasurement.id == entry.id).delete()
    db.commit()


def plan_with(client, db, names, name="Logging Plan", sets="3", reps="8"):
    client.post("/workouts", data=_plan_form(**{
        "name": name, "exercise_name[0][]": names, "exercise_sets[0][]": [sets] * len(names),
        "exercise_reps[0][]": [reps] * len(names), "exercise_rest[0][]": [""] * len(names)}))
    db.expire_all()
    return db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == name))


def post_log(client, day, extra=None, **fields):
    data = {"log_date": DAY, **fields, **(extra or {})}
    return client.post(f"/workouts/day/{day.id}/log", data=data, follow_redirects=False)


def logged(db, name):
    db.expire_all()
    return db.scalar(select(WorkoutExerciseLog).where(WorkoutExerciseLog.name == name).order_by(WorkoutExerciseLog.id.desc()))


def test_a_ticked_matched_exercise_gets_the_workbook_estimate_and_a_snapshot(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Logging Estimate Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    r = post_log(client, day, **{f"completed[{ex.id}]": "on", f"weight_value[{ex.id}]": "185", f"weight_unit[{ex.id}]": "lb",
                                 f"sets_value[{ex.id}]": "4", f"reps_value[{ex.id}]": "8",
                                 f"style_value[{ex.id}]": "Hypertrophy / General"})
    assert r.status_code == 303
    row = logged(db, "Bench Press")
    kg = 200 / calories.LB_PER_KG
    assert (row.db_exercise, row.area, row.equipment, row.met, row.style) == ("Bench Press", "Chest", "Barbell", 3.5, "Hypertrophy / General")
    assert row.sets == 4 and row.reps_value == 8 and row.body_weight_lb == 200 and row.kcal_note is None
    assert row.volume_lb == 185 * 4 * 8
    assert row.gross_kcal == pytest.approx(3.5 * 3.5 * kg / 200 * 2.4 + 1.5 * 3.5 * kg / 200 * 9.0)
    assert row.net_kcal == pytest.approx(2.5 * 3.5 * kg / 200 * 2.4 + 0.5 * 3.5 * kg / 200 * 9.0)
    log = db.get(WorkoutLog, row.workout_log_id)
    assert (log.day_label, log.plan_name) == (day.label, "Logging Estimate Plan")


def test_the_style_defaults_to_the_exercises_own(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Logging Style Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "5"})
    row = logged(db, "Bench Press")
    assert (row.style, row.met) == ("Heavy Strength", 5.0)


def test_no_body_weight_saves_the_log_and_says_why(client, db, me):
    db.query(BodyMeasurement).filter(BodyMeasurement.owner_id == me).delete()
    db.commit()
    plan = plan_with(client, db, ["Bench Press"], name="Logging No Weight Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    assert post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3",
                                    f"reps_value[{ex.id}]": "8"}).status_code == 303
    row = logged(db, "Bench Press")
    assert row.net_kcal is None and "body weight" in row.kcal_note and row.completed is True


def test_a_posted_body_weight_beats_the_latest_weigh_in(client, db, weigh_in):
    plan = plan_with(client, db, ["Push-up"], name="Logging Override Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, body_weight_lb="150", **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "10"})
    assert logged(db, "Push-up").body_weight_lb == 150.0


def test_missing_sets_or_reps_are_named_and_unticked_rows_get_no_estimate(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press", "Push-up"], name="Logging Missing Plan")
    day, bench, push = plan.days[0], plan.days[0].exercises[0], plan.days[0].exercises[1]
    post_log(client, day, **{f"completed[{bench.id}]": "on", f"sets_value[{bench.id}]": "3"})   # reps missing; Push-up not ticked
    assert logged(db, "Bench Press").kcal_note == "Enter reps per set for a calorie estimate."
    assert (logged(db, "Push-up").net_kcal, logged(db, "Push-up").kcal_note) == (None, None)


def test_an_exercise_the_database_does_not_know_logs_without_calories(client, db, weigh_in):
    plan = plan_with(client, db, ["Zorvex Lift"], name="Logging Unknown Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "8"})
    row = logged(db, "Zorvex Lift")
    assert row.db_exercise is None and row.net_kcal is None and "database" in row.kcal_note


@pytest.mark.parametrize("field,value", [
    ("reps_value", "8-12"), ("reps_value", "10.5"), ("reps_value", "0"), ("reps_value", "1000"), ("reps_value", "-3"),
    ("sets_value", "3-4"), ("sets_value", "0"), ("sets_value", "abc"),
    ("weight_value", "-5"), ("minutes_value", "0"), ("style_value", "Not A Style"), ("implements_value", "0")])
def test_ranges_and_impossible_numbers_are_refused_and_the_existing_log_is_kept(client, db, weigh_in, field, value):
    plan = plan_with(client, db, ["Bench Press"], name=f"Logging Refuse {field} {value}")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    good = {f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "8", f"weight_value[{ex.id}]": "100"}
    assert post_log(client, day, **good).status_code == 303
    bad = {**good, f"{field}[{ex.id}]": value}
    r = post_log(client, day, **bad)
    assert r.status_code == 422
    row = logged(db, "Bench Press")
    assert row.reps_value == 8 and row.sets == 3 and row.weight_value == 100.0


def test_a_bad_body_weight_is_refused(client, db):
    plan = plan_with(client, db, ["Bench Press"], name="Logging Bad Weight Plan")
    for bad in ("abc", "0", "-10", "5000", "nan"):
        assert post_log(client, plan.days[0], body_weight_lb=bad).status_code == 422


def test_kilogram_loads_convert_for_volume_only(client, db, weigh_in):
    plan = plan_with(client, db, ["Dumbbell Curl"], name="Logging Kg Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"weight_value[{ex.id}]": "10", f"weight_unit[{ex.id}]": "kg",
                             f"sets_value[{ex.id}]": "2", f"reps_value[{ex.id}]": "10"})
    row = logged(db, "Dumbbell Curl")
    assert row.weight_unit.value == "kg" and row.weight_value == 10.0
    assert row.volume_lb == pytest.approx(10 * calories.LB_PER_KG * 2 * 10)


def test_a_walk_jog_or_run_needs_minutes_and_speed_and_uses_the_compendium(client, db, weigh_in):
    plan = plan_with(client, db, ["Treadmill Run", "Treadmill Incline Walk"], name="Logging Cardio Plan")
    day, run, incline = plan.days[0], plan.days[0].exercises[0], plan.days[0].exercises[1]
    post_log(client, day, **{f"completed[{run.id}]": "on", f"minutes_value[{run.id}]": "30",
                             f"completed[{incline.id}]": "on", f"minutes_value[{incline.id}]": "20"})
    assert logged(db, "Treadmill Run").kcal_note == "Enter speed for a calorie estimate."
    assert logged(db, "Treadmill Incline Walk").kcal_note == "Enter incline grade for a calorie estimate."
    post_log(client, day, **{f"completed[{run.id}]": "on", f"minutes_value[{run.id}]": "30", f"speed_value[{run.id}]": "6",
                             f"completed[{incline.id}]": "on", f"minutes_value[{incline.id}]": "20", f"grade_value[{incline.id}]": "8"})
    run_row, incline_row = logged(db, "Treadmill Run"), logged(db, "Treadmill Incline Walk")
    assert (run_row.met, run_row.compendium_code, run_row.speed_mph) == (9.3, "12050", 6.0)
    assert (incline_row.met, incline_row.compendium_code, incline_row.grade_pct) == (7.0, "17035", 8.0)


def test_an_exercise_added_on_the_day_is_matched_by_fuzzy_name_and_estimated(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Logging Extra Plan")
    day = plan.days[0]
    r = post_log(client, day, extra={
        "extra-0-exercise": "DB incline press", "extra-0-weight_value": "40", "extra-0-weight_unit": "lb",
        "extra-0-sets_value": "3", "extra-0-reps_value": "10", "extra-0-implements_value": "2",
        "extra-1-exercise": "", "extra-1-sets_value": "9"})            # a blank row is ignored
    assert r.status_code == 303
    row = logged(db, "Dumbbell Incline Press")
    assert row.exercise_id is None and row.db_exercise == "Dumbbell Incline Press" and row.completed is True
    assert row.net_kcal > 0 and row.volume_lb == 40 * 2 * 3 * 10 and row.implements == 2
    db.expire_all()
    assert db.query(WorkoutExerciseLog).filter(WorkoutExerciseLog.name == "").count() == 0


def test_an_added_exercise_that_is_not_in_the_database_is_refused_with_suggestions(client, db):
    plan = plan_with(client, db, ["Bench Press"], name="Logging Extra Refuse Plan")
    r = post_log(client, plan.days[0], extra={"extra-0-exercise": "Squats", "extra-0-sets_value": "3", "extra-0-reps_value": "5"})
    assert r.status_code == 422 and "Squats" in r.text
    r = post_log(client, plan.days[0], extra={"extra-0-exercise": "Zzz Quasar Lift"})
    assert r.status_code == 422


def test_saving_again_replaces_the_days_log_and_recalculates(client, db, weigh_in):
    plan = plan_with(client, db, ["Push-up"], name="Logging Replace Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "10"})
    first = logged(db, "Push-up").net_kcal
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "6", f"reps_value[{ex.id}]": "10"})
    db.expire_all()
    rows = db.scalars(select(WorkoutExerciseLog).where(WorkoutExerciseLog.name == "Push-up")).all()
    assert len(rows) == 1 and rows[0].net_kcal > first


def test_the_log_form_shows_the_estimate_the_total_and_last_times_numbers(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Logging Form Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"weight_value[{ex.id}]": "135", f"weight_unit[{ex.id}]": "lb",
                             f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "8"})
    page = client.get(f"/workouts/day/{day.id}/log", params={"log_date": DAY}).text
    assert "kcal" in page and 'name="body_weight_lb"' in page and 'value="200"' in page
    assert f'name="sets_value[{ex.id}]" value="3"' in page and f'name="reps_value[{ex.id}]" value="8"' in page
    other = client.get(f"/workouts/day/{day.id}/log", params={"log_date": "2026-02-09"}).text   # a day with no log yet
    assert "Last time" in other and "135" in other                                              # history pre-fills the next session


def test_history_follows_the_exercise_into_a_later_plan(client, db, weigh_in):
    first = plan_with(client, db, ["Bench Press"], name="Logging History One")
    ex = first.days[0].exercises[0]
    post_log(client, first.days[0], **{f"completed[{ex.id}]": "on", f"weight_value[{ex.id}]": "155", f"weight_unit[{ex.id}]": "lb",
                                       f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "5"})
    second = plan_with(client, db, ["Barbell Bench Press"], name="Logging History Two")      # same exercise, new plan, new wording
    page = client.get(f"/workouts/day/{second.days[0].id}/log", params={"log_date": "2026-03-01"}).text
    assert "Last time" in page and "155" in page
