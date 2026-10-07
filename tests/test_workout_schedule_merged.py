"""The plan editor and its weekly schedule are one form: each day carries its own weekday boxes."""

import re

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import WorkoutPlan, WorkoutPlanDay
from test_workouts import _plan_form


@pytest.fixture(autouse=True)
def _clean(client, me):
    def wipe():
        with SessionLocal() as s:
            s.query(WorkoutPlan).filter(WorkoutPlan.owner_id == me, WorkoutPlan.name.like("Merge Test%")).delete(synchronize_session=False)
            s.commit()
    wipe()
    yield
    wipe()


def make_plan(client, me, **fields):
    data = _plan_form(name="Merge Test Plan", **{"day_label[]": ["Push", "Pull"], "exercise_name[0][]": ["Push-up"], "exercise_sets[0][]": ["3"],
                                                 "exercise_reps[0][]": ["10"], "exercise_rest[0][]": [""], "exercise_name[1][]": ["Pull-up"],
                                                 "exercise_sets[1][]": ["3"], "exercise_reps[1][]": ["5"], "exercise_rest[1][]": [""]} | fields)
    r = client.post("/workouts", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text[:200]
    with SessionLocal() as s:
        return s.scalar(select(WorkoutPlan.id).where(WorkoutPlan.owner_id == me, WorkoutPlan.name == "Merge Test Plan"))


def days_of(plan_id):
    with SessionLocal() as s:
        return [(d.label, d.weekdays) for d in s.scalars(select(WorkoutPlanDay).where(WorkoutPlanDay.plan_id == plan_id).order_by(WorkoutPlanDay.position))]


def test_a_new_plan_can_be_scheduled_as_it_is_created(client, db, me):
    pid = make_plan(client, me, **{"weekdays[0][]": ["M", "R"], "weekdays_present[0]": ["1"], "weekdays[1][]": ["T"], "weekdays_present[1]": ["1"]})
    assert days_of(pid) == [("Push", "MR"), ("Pull", "T")]


def test_boxes_come_back_in_week_order_and_junk_is_dropped(client, db, me):
    pid = make_plan(client, me, **{"weekdays[0][]": ["F", "M", "X", "W"], "weekdays_present[0]": ["1"]})
    assert days_of(pid)[0][1] == "MWF"


def test_the_editor_shows_the_boxes_inside_each_day_and_no_separate_schedule_form(client, db, me):
    pid = make_plan(client, me, **{"weekdays[0][]": ["M"], "weekdays_present[0]": ["1"]})
    page = client.get(f"/workouts/{pid}/edit").text
    first_day = page.split("data-day>")[1].split("</fieldset>")[0]
    assert 'name="weekdays[0][]" value="M" checked' in first_day.replace("\n", " ") or re.search(r'name="weekdays\[0\]\[\]" value="M"\s+checked', first_day)
    assert "Save schedule" not in page and f'action="/workouts/{pid}/schedule"' not in page
    assert page.count('name="weekdays_present[') >= 2


def test_saving_the_editor_updates_the_schedule_and_unticking_everything_clears_it(client, db, me):
    pid = make_plan(client, me, **{"weekdays[0][]": ["M"], "weekdays_present[0]": ["1"]})
    with SessionLocal() as s:
        ids = [d.id for d in s.scalars(select(WorkoutPlanDay).where(WorkoutPlanDay.plan_id == pid).order_by(WorkoutPlanDay.position))]
    form = {"name": "Merge Test Plan", "day_id[]": [str(i) for i in ids], "day_label[]": ["Push", "Pull"], "weekdays_present[0]": ["1"], "weekdays_present[1]": ["1"],
            "weekdays[1][]": ["S", "U"]}
    client.post(f"/workouts/{pid}", data=form)
    assert days_of(pid) == [("Push", None), ("Pull", "SU")]


def test_a_save_that_does_not_mention_the_schedule_leaves_it_alone(client, db, me):
    pid = make_plan(client, me, **{"weekdays[0][]": ["M"], "weekdays_present[0]": ["1"]})
    with SessionLocal() as s:
        ids = [d.id for d in s.scalars(select(WorkoutPlanDay).where(WorkoutPlanDay.plan_id == pid).order_by(WorkoutPlanDay.position))]
    client.post(f"/workouts/{pid}", data={"name": "Merge Test Plan", "day_id[]": [str(i) for i in ids], "day_label[]": ["Push renamed", "Pull"]})
    assert days_of(pid) == [("Push renamed", "M"), ("Pull", None)]


def test_the_old_schedule_route_still_works(client, db, me):
    pid = make_plan(client, me)
    with SessionLocal() as s:
        day_id = s.scalar(select(WorkoutPlanDay.id).where(WorkoutPlanDay.plan_id == pid).order_by(WorkoutPlanDay.position))
    assert client.post(f"/workouts/{pid}/schedule", data={f"weekdays[{day_id}][]": ["T", "R"]}, follow_redirects=False).status_code == 303
    assert days_of(pid)[0][1] == "TR"


def test_a_day_added_in_the_browser_gets_its_boxes_renumbered(client, db):
    js = client.get("/static/js/workouts.js").text
    assert "weekdays[" in js


def test_the_dashboard_week_card_summarises_progress(client, db, me):
    from datetime import date
    from app.models import WEEKDAY_LETTERS
    today = WEEKDAY_LETTERS[date.today().weekday()]
    make_plan(client, me, **{"weekdays[0][]": [today], "weekdays_present[0]": ["1"]})
    page = client.get("/dashboard").text
    assert "workout-week-summary" in page and "0 of 1" in page and "Next up: today" in page
