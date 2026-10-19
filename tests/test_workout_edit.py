"""Editing a logged workout from the Journal (past ones too), and the warning on submit when not every exercise is done."""

import html
import re

import pytest
from sqlalchemy import select

from app.models import WorkoutExerciseLog, WorkoutLog
from test_workout_logging import DAY, plan_with, post_log, weigh_in  # noqa: F401

JOURNAL = "/measurements?tab=journal"


def journal(client):
    return html.unescape(client.get(JOURNAL).text)


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(WorkoutLog).filter(WorkoutLog.owner_id == me).delete()
    db.commit()


def test_a_logged_plan_workout_in_the_journal_has_an_edit_button_to_its_log_form(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Edit Journal Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on"})
    page = journal(client)
    assert f'href="/workouts/day/{day.id}/log?log_date={DAY}&return_to=journal"' in page and ">Edit</a>" in page


def test_a_free_or_quick_workout_in_the_journal_has_an_edit_button_too(client, db, me, weigh_in):
    client.post("/workouts/quick", data={"activity": "walk", "minutes": "20", "log_date": DAY}, follow_redirects=False)
    log = db.scalar(select(WorkoutLog).where(WorkoutLog.owner_id == me, WorkoutLog.plan_day_id.is_(None)))
    assert f'href="/workouts/free/{log.id}/log?return_to=journal"' in journal(client)


def test_editing_a_past_workout_replaces_it_and_goes_back_to_the_journal(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Edit Replace Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "8", f"weight_value[{ex.id}]": "135", f"weight_unit[{ex.id}]": "lb"})
    form = client.get(f"/workouts/day/{day.id}/log", params={"log_date": DAY, "return_to": "journal"}).text
    assert 'name="return_to" value="journal"' in form and 'value="135"' in form             # the saved numbers come back into the form
    r = post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "4", f"reps_value[{ex.id}]": "10", f"weight_value[{ex.id}]": "145", f"weight_unit[{ex.id}]": "lb", "return_to": "journal"})
    assert r.status_code == 303 and r.headers["location"] == JOURNAL
    db.expire_all()
    rows = db.scalars(select(WorkoutExerciseLog).where(WorkoutExerciseLog.name == "Bench Press")).all()
    assert len(rows) == 1 and (rows[0].sets, rows[0].reps_value, rows[0].weight_value) == (4, 10, 145.0)
    assert db.query(WorkoutLog).filter(WorkoutLog.plan_day_id == day.id).count() == 1


def test_a_plan_log_still_returns_to_today_unless_it_came_from_the_journal(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Edit Today Plan")
    day = plan.days[0]
    assert post_log(client, day).headers["location"] == "/today"
    assert post_log(client, day, return_to="https://example.com").headers["location"] == "/today"        # only the journal may be asked for


def test_editing_a_free_workout_can_return_to_the_journal(client, db, me, weigh_in):
    client.post("/workouts/quick", data={"activity": "walk", "minutes": "20", "log_date": DAY}, follow_redirects=False)
    log = db.scalar(select(WorkoutLog).where(WorkoutLog.owner_id == me, WorkoutLog.plan_day_id.is_(None)))
    page = client.get(f"/workouts/free/{log.id}/log", params={"return_to": "journal"}).text
    assert 'name="return_to" value="journal"' in page


# ---- the warning when not every exercise is done ------------------------------------------------------------------------------------------------

def test_the_log_form_carries_the_incomplete_warning_for_the_script(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press", "Back Squat"], name="Edit Warn Plan")
    page = client.get(f"/workouts/day/{plan.days[0].id}/log").text
    form_tag = re.search(r'<form[^>]*id="workout-log-form"[^>]*>', page).group(0)
    assert "data-confirm-incomplete" in form_tag
    assert page.count('name="completed[') == 2
    assert "workout-log.js" in page


def test_the_warning_script_checks_the_done_boxes_and_asks_before_submitting():
    js = open("app/static/js/workout-log.js", encoding="utf-8").read()
    assert r".\n\nSubmit the workout anyway?" in js         # one line: a raw line break inside the quotes would be a syntax error that kills the whole script
    assert "data-confirm-incomplete" in js and "confirm(" in js and 'name^="completed["' in js and "preventDefault" in js
