from datetime import date

from sqlalchemy import select

from app.models import WorkoutExerciseLog, WorkoutLog, WorkoutPlan, WorkoutPlanDay
from app.routers.journal import workouts_for
from test_workouts import _form_from_plan, _two_day_plan  # helpers: a two-day plan, Day A logged 2026-01-05, Day B logged 2026-01-06


def _without_day_b(plan):
    form = _form_from_plan(plan)
    form = {k: v for k, v in form.items() if not k.endswith("[1][]")}
    form["day_label[]"], form["day_id[]"] = form["day_label[]"][:1], form["day_id[]"][:1]
    return form


def test_removing_a_day_keeps_its_logged_history(client, db):
    plan = _two_day_plan(client, db, name="History Day Plan")
    owner_id, plan_name = plan.owner_id, plan.name
    client.post(f"/workouts/{plan.id}", data=_without_day_b(plan))
    db.expire_all()
    log = db.scalar(select(WorkoutLog).where(WorkoutLog.owner_id == owner_id, WorkoutLog.log_date == date(2026, 1, 6),
                                             WorkoutLog.plan_name == plan_name))
    assert log is not None and log.plan_day_id is None and log.day_label == "Day B"
    assert [(el.exercise_id, el.name, el.completed) for el in log.exercise_logs] == [(None, "Plank", True)]


def test_removing_an_exercise_keeps_what_was_logged_for_it(client, db):
    plan = _two_day_plan(client, db, name="History Exercise Plan")
    form = _form_from_plan(plan)
    for key in ("id", "name", "sets", "reps", "rest"):
        form[f"exercise_{key}[0][]"] = form[f"exercise_{key}[0][]"][1:]   # drop Push-up, keep Squat
    client.post(f"/workouts/{plan.id}", data=form)
    db.expire_all()
    kept = db.scalar(select(WorkoutExerciseLog).where(WorkoutExerciseLog.name == "Push-up",
                                                      WorkoutExerciseLog.weight_value == 20.0))
    assert kept is not None and kept.exercise_id is None and kept.completed is True


def test_logging_a_new_day_never_replaces_a_removed_days_history(client, db):
    plan = _two_day_plan(client, db, name="History Replace Plan")
    plan_name = plan.name
    form = _without_day_b(plan)
    form["day_label[]"].append("Day C")
    form["day_id[]"].append("")
    form["exercise_id[1][]"], form["exercise_name[1][]"] = [""], ["Lunge"]
    form["exercise_sets[1][]"], form["exercise_reps[1][]"], form["exercise_rest[1][]"] = ["3"], ["10"], [""]
    client.post(f"/workouts/{plan.id}", data=form)
    db.expire_all()
    day_c = db.scalar(select(WorkoutPlanDay).where(WorkoutPlanDay.label == "Day C"))
    lunge = day_c.exercises[0]
    assert client.post(f"/workouts/day/{day_c.id}/log", data={
        "log_date": "2026-01-06", f"completed[{lunge.id}]": "on"}, follow_redirects=False).status_code == 303
    db.expire_all()
    labels = sorted(l.day_label for l in db.scalars(select(WorkoutLog).where(
        WorkoutLog.log_date == date(2026, 1, 6), WorkoutLog.plan_name == plan_name)))
    assert labels == ["Day B", "Day C"]


def test_the_journal_uses_the_saved_label_when_the_day_is_gone(client, db):
    plan = _two_day_plan(client, db, name="History Journal Plan")
    owner_id = plan.owner_id
    client.post(f"/workouts/{plan.id}", data=_without_day_b(plan))
    db.expire_all()
    rows = workouts_for(db, owner_id, date(2026, 1, 6))
    assert [r["label"] for r in rows if r["label"] == "Day B"] == ["Day B"]


def test_the_edit_page_says_history_is_kept_instead_of_threatening_to_delete_it(client, db):
    plan = _two_day_plan(client, db, name="History Note Plan")
    page = client.get(f"/workouts/{plan.id}/edit").text
    assert "stay in your history" in page and "will also delete" not in page
