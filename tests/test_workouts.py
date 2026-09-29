from datetime import date

from sqlalchemy import select

from app.models import WorkoutPlan, WorkoutSource


def _plan_form(**overrides):
    fields = {
        "name": "My Manual Plan",
        "day_label[]": ["Day 1"],
        "exercise_name[0][]": ["Push-up"],
        "exercise_sets[0][]": ["3"],
        "exercise_reps[0][]": ["10 - 12"],
        "exercise_rest[0][]": [""],
    }
    return {**fields, **overrides}


def test_create_manual_plan(client, db):
    r = client.post("/workouts", data=_plan_form(), follow_redirects=False)
    assert r.status_code == 303
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "My Manual Plan"))
    assert plan is not None
    assert plan.source == WorkoutSource.MANUAL
    assert plan.ended_on is None  # Active
    assert len(plan.days) == 1
    assert plan.days[0].label == "Day 1"
    assert plan.days[0].exercises[0].name == "Push-up"
    assert plan.days[0].exercises[0].reps_text == "10 - 12"


def test_activating_a_plan_ends_the_previous_active_one(client, db):
    client.post("/workouts", data=_plan_form(name="Plan A"))
    client.post("/workouts", data=_plan_form(name="Plan B"))
    plan_a = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "Plan A"))
    plan_b = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "Plan B"))
    db.refresh(plan_a)
    assert plan_a.ended_on is not None  # ended when Plan B was created as the new Active plan
    assert plan_b.ended_on is None


def test_schedule_sets_weekdays_on_a_plan_day(client, db):
    client.post("/workouts", data=_plan_form())
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "My Manual Plan"))
    day_id = plan.days[0].id
    r = client.post(f"/workouts/{plan.id}/schedule", data={f"weekdays[{day_id}]": "MWF"}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(plan)
    assert plan.days[0].weekdays == "MWF"


def test_edit_replaces_days_and_exercises(client, db):
    client.post("/workouts", data=_plan_form())
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "My Manual Plan"))
    r = client.post(f"/workouts/{plan.id}", data=_plan_form(
        **{"exercise_name[0][]": ["Sit-up"]}), follow_redirects=False)
    assert r.status_code == 303
    db.refresh(plan)
    assert len(plan.days[0].exercises) == 1
    assert plan.days[0].exercises[0].name == "Sit-up"
