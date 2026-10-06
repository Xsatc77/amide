import html
from datetime import date

from sqlalchemy import select

from app.models import JournalEntry, User, WorkoutLog, WorkoutPlan
from app.routers.journal import workouts_for
from test_workout_logging import DAY, plan_with, post_log, weigh_in  # noqa: F401


def journal(client):
    return html.unescape(client.get("/measurements?tab=journal").text)


def test_the_journal_tab_has_a_log_workout_button_and_a_day_picker(client, db):
    plan = plan_with(client, db, ["Bench Press"], name="Journal Button Plan")
    text = journal(client)
    assert 'data-action="open-workout-log"' in text and "Log Workout" in text
    assert 'id="workout-log-dialog"' in text and f'<option value="{plan.days[0].id}">' in text
    assert 'name="log_date"' in text


def test_without_a_plan_the_dialog_points_to_creating_one(client, db):
    db.query(WorkoutPlan).delete()
    db.commit()
    text = journal(client)
    assert "no workout plan yet" in text.lower() and 'data-action="open-workout-log"' in text


def test_the_picker_redirects_to_that_days_log_form(client, db):
    plan = plan_with(client, db, ["Bench Press"], name="Journal Redirect Plan")
    day = plan.days[0]
    r = client.get("/workouts/log", params={"plan_day_id": day.id, "log_date": "2026-02-03"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/workouts/day/{day.id}/log?log_date=2026-02-03"
    assert client.get("/workouts/log", params={"plan_day_id": 999999}, follow_redirects=False).status_code == 404


def test_a_logged_workout_shows_in_the_journal_with_its_estimated_calories(client, db, weigh_in):
    plan = plan_with(client, db, ["Push-up"], name="Journal Kcal Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "10"})
    rows = workouts_for(db, plan.owner_id, date.fromisoformat(DAY))
    assert rows[0]["net_kcal"] > 0 and rows[0]["gross_kcal"] > rows[0]["net_kcal"]
    text = journal(client)
    assert f"about {rows[0]['net_kcal']} kcal" in text and "estimated" in text


def test_a_workout_with_no_journal_entry_still_appears_as_its_own_row(client, db, weigh_in):
    plan = plan_with(client, db, ["Push-up"], name="Journal Orphan Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "10"})
    assert db.scalar(select(JournalEntry).where(JournalEntry.entry_date == date.fromisoformat(DAY))) is None
    text = journal(client)
    assert "Feb 2, 2026" in text and day.label in text


def test_other_peoples_workouts_never_appear_in_my_journal(client, db):
    other = User(username="journalother", username_key="journalother", password_hash="x")
    db.add(other)
    db.commit()
    try:
        db.add(WorkoutLog(owner_id=other.id, plan_day_id=None, day_label="Secret Day", plan_name="Their Plan",
                          log_date=date(2026, 2, 4)))
        db.commit()
        assert "Secret Day" not in journal(client)
    finally:
        db.query(User).filter(User.id == other.id).delete()
        db.commit()


def test_a_workout_without_estimates_shows_no_kcal(client, db):
    plan = plan_with(client, db, ["Zorvex Lift"], name="Journal No Kcal Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on"})
    row = workouts_for(db, plan.owner_id, date.fromisoformat(DAY))[0]
    assert row["net_kcal"] is None
