import re

import pytest
from sqlalchemy import select

from app.models import WorkoutExercise, WorkoutPlan
from test_workout_logging import post_log, weigh_in  # noqa: F401
from test_workouts import _form_from_plan, _plan_form


def make_plan(client, db, names, name):
    client.post("/workouts", data=_plan_form(**{
        "name": name, "exercise_name[0][]": names, "exercise_sets[0][]": ["3"] * len(names),
        "exercise_reps[0][]": ["8"] * len(names), "exercise_rest[0][]": [""] * len(names)}))
    db.expire_all()
    return db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == name))


def stored(db, plan):
    db.expire_all()
    return {ex.name: (ex.db_exercise, ex.db_exercise_confirmed) for ex in db.get(WorkoutPlan, plan.id).days[0].exercises}


def test_saving_a_plan_matches_confident_names_and_leaves_the_rest_open(client, db):
    plan = make_plan(client, db, ["Barbell Bench Press", "Squats", "Running", "Zorvex Lift"], "Match Save Plan")
    assert stored(db, plan) == {"Barbell Bench Press": ("Bench Press", False), "Squats": (None, False),
                                "Running": ("Treadmill Run", False), "Zorvex Lift": (None, False)}


def test_renaming_resets_the_match_and_unrelated_edits_keep_a_confirmed_one(client, db):
    plan = make_plan(client, db, ["Squats"], "Match Rename Plan")
    ex = plan.days[0].exercises[0]
    assert client.post(f"/workouts/exercises/{ex.id}/match", data={"suggested": "Back Squat"}, follow_redirects=False).status_code == 303
    assert stored(db, plan) == {"Squats": ("Back Squat", True)}
    form = _form_from_plan(plan)
    form["exercise_sets[0][]"] = ["5"]                                        # not a rename
    client.post(f"/workouts/{plan.id}", data=form)
    assert stored(db, plan) == {"Squats": ("Back Squat", True)}
    db.expire_all()
    form = _form_from_plan(db.get(WorkoutPlan, plan.id))
    form["exercise_name[0][]"] = ["Bench Press"]                              # a rename: matched afresh
    client.post(f"/workouts/{plan.id}", data=form)
    assert stored(db, plan) == {"Bench Press": ("Bench Press", False)}


def test_the_confirm_endpoint_accepts_typed_names_rejects_unknown_ones_and_clears_on_blank(client, db):
    plan = make_plan(client, db, ["Zorvex Lift"], "Match Confirm Plan")
    ex = plan.days[0].exercises[0]
    url = f"/workouts/exercises/{ex.id}/match"
    assert client.post(url, data={"db_exercise": "db incline press"}, follow_redirects=False).status_code == 303
    assert stored(db, plan) == {"Zorvex Lift": ("Dumbbell Incline Press", True)}
    assert client.post(url, data={"db_exercise": "no such exercise anywhere"}, follow_redirects=False).status_code == 422
    assert stored(db, plan) == {"Zorvex Lift": ("Dumbbell Incline Press", True)}
    assert client.post(url, data={"db_exercise": ""}, follow_redirects=False).status_code == 303
    assert stored(db, plan) == {"Zorvex Lift": (None, True)}                     # "no estimate", and it stays that way
    form = _form_from_plan(db.get(WorkoutPlan, plan.id))
    client.post(f"/workouts/{plan.id}", data=form)
    assert stored(db, plan) == {"Zorvex Lift": (None, True)}


def test_an_unknown_exercise_id_is_a_404(client):
    assert client.post("/workouts/exercises/999999/match", data={"db_exercise": "Bench Press"}).status_code == 404


def test_the_edit_page_shows_matches_and_offers_suggestions(client, db):
    plan = make_plan(client, db, ["Barbell Bench Press", "Squats"], "Match Page Plan")
    page = client.get(f"/workouts/{plan.id}/edit").text
    bench, squats = plan.days[0].exercises
    assert "Bench Press" in page and f'id="match-{bench.id}"' in page
    assert re.search(rf'form="match-{squats.id}"[^>]*name="suggested"[^>]*value="Back Squat"', page) or \
        re.search(rf'name="suggested"[^>]*value="Back Squat"[^>]*form="match-{squats.id}"', page)
    assert 'id="exercise-names"' in page


def test_the_log_uses_the_confirmed_match(client, db, weigh_in):
    plan = make_plan(client, db, ["Squats"], "Match Log Plan")
    ex = plan.days[0].exercises[0]
    client.post(f"/workouts/exercises/{ex.id}/match", data={"suggested": "Back Squat"})
    post_log(client, plan.days[0], **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "5"})
    from app.models import WorkoutExerciseLog
    db.expire_all()
    row = db.scalar(select(WorkoutExerciseLog).where(WorkoutExerciseLog.name == "Squats"))
    assert row.db_exercise == "Back Squat" and row.net_kcal > 0
