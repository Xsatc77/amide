"""Ending and deleting a workout plan; logged workouts survive either."""

from datetime import date

import pytest
from sqlalchemy import select

from app import config
from app.db import SessionLocal
from app.models import WorkoutLog, WorkoutPlan, WorkoutPlanDay, WorkoutSource
from photo_helpers import other_client

TODAY = date.today()


@pytest.fixture
def plan(me):
    config.ensure_dirs()
    pdf_name = "endtest0000000000000000000000.pdf"
    (config.WORKOUT_PDF_DIR / pdf_name).write_bytes(b"%PDF-1.4 test")
    with SessionLocal() as s:
        p = WorkoutPlan(owner_id=me, name="End Delete Plan", source=WorkoutSource.PDF, source_pdf_filename=pdf_name, started_on=TODAY)
        s.add(p)
        s.flush()
        day = WorkoutPlanDay(plan_id=p.id, position=0, label="Day A")
        s.add(day)
        s.flush()
        s.add(WorkoutLog(owner_id=me, plan_day_id=day.id, day_label="Day A", plan_name="End Delete Plan", log_date=TODAY))
        s.commit()
        pid = p.id
    yield pid, pdf_name
    with SessionLocal() as s:
        s.query(WorkoutLog).filter(WorkoutLog.plan_name == "End Delete Plan").delete()
        s.query(WorkoutPlan).filter(WorkoutPlan.name == "End Delete Plan").delete()
        s.commit()
    (config.WORKOUT_PDF_DIR / pdf_name).unlink(missing_ok=True)


def test_ending_a_plan_marks_it_ended_and_it_can_be_made_active_again(client, db, plan):
    r = client.post(f"/workouts/{plan[0]}/end", follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(WorkoutPlan, plan[0]).ended_on == TODAY
    assert "Make Active" in client.get("/workouts").text
    client.post(f"/workouts/{plan[0]}/activate")
    with SessionLocal() as s:
        assert s.get(WorkoutPlan, plan[0]).ended_on is None


def test_deleting_a_plan_removes_it_its_days_and_its_pdf_but_keeps_logged_workouts(client, db, plan):
    r = client.post(f"/workouts/{plan[0]}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/workouts"
    with SessionLocal() as s:
        assert s.get(WorkoutPlan, plan[0]) is None
        assert s.query(WorkoutPlanDay).filter_by(plan_id=plan[0]).count() == 0
        log = s.scalar(select(WorkoutLog).where(WorkoutLog.plan_name == "End Delete Plan"))
        assert log is not None and log.plan_day_id is None and log.day_label == "Day A"
    assert not (config.WORKOUT_PDF_DIR / plan[1]).exists()


def test_someone_elses_plan_cannot_be_ended_or_deleted(client, db, plan):
    with other_client("planother") as member:
        assert member.post(f"/workouts/{plan[0]}/end").status_code == 404
        assert member.post(f"/workouts/{plan[0]}/delete").status_code == 404
    with SessionLocal() as s:
        assert s.get(WorkoutPlan, plan[0]) is not None


def test_the_plans_page_has_end_and_delete_buttons(client, db, plan):
    page = client.get("/workouts").text
    assert f'action="/workouts/{plan[0]}/end"' in page and f'action="/workouts/{plan[0]}/delete"' in page and "data-confirm" in page
