import re
from datetime import date

import pytest
from sqlalchemy import event, select

from app.db import engine
from app.models import WorkoutExerciseLog, WorkoutLog, WorkoutPlan
from app.routers.journal import journal_tab_context
from app.workouts.exercise_match import match_exercise
from test_workout_logging import DAY, plan_with, post_log, weigh_in  # noqa: F401
from test_workouts import _form_from_plan


def rendered_extras(client, day):
    """What a browser would post back for the 'Added today' rows: every named input of those rows."""
    page = client.get(f"/workouts/day/{day.id}/log", params={"log_date": DAY}).text
    body = page.split('data-extra-rows>', 1)[1].split("</tbody>", 1)[0]
    data = {}
    for tag in re.findall(r"<(?:input|select)[^>]*>", body):
        name = re.search(r'name="(extra-\d+-\w+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if name and value and value.group(1) != "":
            data[name.group(1)] = value.group(1)
    return data


def drop_exercises(client, db, plan, keep):
    form = _form_from_plan(db.get(WorkoutPlan, plan.id))
    for key in ("id", "name", "sets", "reps", "rest"):
        form[f"exercise_{key}[0][]"] = [v for v, n in zip(form[f"exercise_{key}[0][]"], form["exercise_name[0][]"]) if n in keep]
    client.post(f"/workouts/{plan.id}", data=form)
    db.expire_all()


def test_resaving_a_log_whose_plan_exercises_were_removed_keeps_those_rows_as_they_were(client, db, weigh_in):
    plan = plan_with(client, db, ["Push-up", "Squats", "Plank"], name="Review Orphan Plan")
    day = plan.days[0]
    pushup, squats, plank = day.exercises
    post_log(client, day, **{f"completed[{squats.id}]": "on", f"sets_value[{squats.id}]": "3", f"reps_value[{squats.id}]": "5",
                             f"sets_value[{pushup.id}]": "3", f"reps_value[{pushup.id}]": "10"})   # Push-up logged NOT done
    drop_exercises(client, db, plan, keep={"Plank"})
    extras = rendered_extras(client, day)
    assert client.post(f"/workouts/day/{day.id}/log", data={"log_date": DAY, **extras}, follow_redirects=False).status_code == 303
    db.expire_all()
    rows = {r.name: r for r in db.scalars(select(WorkoutExerciseLog).where(WorkoutExerciseLog.name.in_(["Push-up", "Squats"])))}
    assert rows["Push-up"].completed is False and rows["Push-up"].net_kcal is None
    assert rows["Squats"].completed is True and rows["Squats"].db_exercise is None and rows["Squats"].sets == 3


def test_a_cleared_match_stays_cleared_in_the_log(client, db, weigh_in):
    plan = plan_with(client, db, ["Lat Pulldown"], name="Review Cleared Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    assert ex.db_exercise is not None                                       # matched on save
    assert client.post(f"/workouts/exercises/{ex.id}/match", data={"db_exercise": ""}, follow_redirects=False).status_code == 303
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "10"})
    db.expire_all()
    row = db.scalar(select(WorkoutExerciseLog).where(WorkoutExerciseLog.name == "Lat Pulldown"))
    assert row.db_exercise is None and row.net_kcal is None and "database" in row.kcal_note
    assert "no calorie estimate" in client.get(f"/workouts/day/{day.id}/log", params={"log_date": DAY}).text


@pytest.mark.parametrize("typed", ["Barbell Squat", "Squat (Barbell)", "Dumbbell Squat", "Squat Machine", "Bench Press Machine"])
def test_a_name_that_only_resembles_several_exercises_is_a_suggestion_not_a_guess(typed):
    assert not match_exercise(typed).confident


@pytest.mark.parametrize("typed,name", [("Lat Pulldown", {"Cable Lat Pulldown", "Lat Pulldown Machine"}),
                                         ("Leg Extension", {"Leg Extension Machine"}),
                                         ("Barbell Biceps Curl", {"Barbell Curl"}), ("Flat Barbell Bench Press", {"Bench Press"}),
                                         ("Triceps Pushdown", {"Cable Triceps Pushdown", "Rope Triceps Pushdown"})])
def test_sensible_fuzzy_matches_still_land(typed, name):
    assert match_exercise(typed).exercise.name in name


def test_the_plan_editor_warns_before_a_match_button_discards_unsaved_edits():
    from pathlib import Path
    js = Path("app/static/js/workouts.js").read_text(encoding="utf-8")
    assert "unsaved" in js and 'form^="match-"' in js


def test_the_journal_tab_does_not_query_per_workout_day(client, db, me, weigh_in):
    for i in range(40):
        db.add(WorkoutLog(owner_id=me, plan_day_id=None, day_label=f"Day {i}", plan_name="P", log_date=date(2025, 1, 1).fromordinal(date(2025, 1, 1).toordinal() + i),
                          exercise_logs=[WorkoutExerciseLog(name="Push-Up", completed=True, net_kcal=10.0, gross_kcal=14.0)]))
    db.commit()
    count = 0

    def tick(*args, **kwargs):
        nonlocal count
        count += 1
    event.listen(engine, "before_cursor_execute", tick)
    try:
        journal_tab_context(db, me)
    finally:
        event.remove(engine, "before_cursor_execute", tick)
    assert count < 30, count
