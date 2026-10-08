"""US or metric display units: stored in US, converted when typed in and when shown."""

import pytest
from sqlalchemy import select

from app import units
from app.db import SessionLocal
from app.models import BodyMeasurement, User


@pytest.fixture
def metric(client, me):
    def set_units(value):
        with SessionLocal() as s:
            s.get(User, me).units = value
            s.query(BodyMeasurement).filter(BodyMeasurement.owner_id == me).delete()
            s.commit()
    set_units("metric")
    yield
    set_units("us")


def test_conversions_round_trip():
    m, us = units.Units("metric"), units.Units("us")
    assert m.weight(220.462) == 100.0 and us.weight(220.462) == 220.462
    assert m.length(10) == 25.4 and abs(m.length_in(25.4) - 10) < 1e-9
    assert m.volume(64) == 1890 and abs(m.volume_in(1000) - 33.814) < 0.001
    assert m.speed(6.0) == 9.7 and abs(m.speed_in(10) - 6.2137) < 0.001
    assert m.weight(None) is None and m.weight_in(None) is None


def test_the_units_setting_is_validated_and_saved(client, db, me):
    assert client.post("/settings/units", data={"units": "stone"}).status_code == 422
    assert client.post("/settings/units", data={"units": "metric"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(User, me).units == "metric"
    assert 'value="metric" selected' in client.get("/settings").text
    client.post("/settings/units", data={"units": "us"})


def test_metric_entries_are_stored_in_us_units_and_shown_in_metric(client, db, me, metric):
    client.post("/measurements", data={"measured_at": "2026-10-01", "weight_lbs": "80", "waist_in": "90"})
    with SessionLocal() as s:
        row = s.scalar(select(BodyMeasurement).where(BodyMeasurement.owner_id == me))
    assert abs(row.weight_lbs - 176.37) < 0.01 and abs(row.waist_in - 35.433) < 0.01
    page = client.get("/measurements").text
    assert "Weight (kg)" in page and "Waist (cm)" in page
    assert ">80.0<" in page and ">90.0<" in page


def test_us_users_see_no_change(client, db, me):
    client.post("/measurements", data={"measured_at": "2026-10-02", "weight_lbs": "180"})
    page = client.get("/measurements").text
    assert "Weight (lbs)" in page and ">180.0<" in page or ">180<" in page
    with SessionLocal() as s:
        s.query(BodyMeasurement).filter(BodyMeasurement.owner_id == me).delete()
        s.commit()


def test_metric_water_and_height_in_settings(client, db, me, metric):
    page = client.get("/settings").text
    assert "Height (cm)" in page and "Water goal override (mL)" in page
    client.post("/settings/body-profile", data={"height_in": "180", "water_goal_oz": "2500"})
    db.expire_all()
    user = db.get(User, me)
    assert abs(user.height_in - 70.866) < 0.01 and user.water_goal_oz == 85
    user.height_in = user.water_goal_oz = None
    db.commit()


def test_metric_water_is_typed_in_ml_and_stored_in_ounces(client, db, me, metric):
    from datetime import date

    from app.models import WaterLog
    with SessionLocal() as s:
        s.query(WaterLog).filter(WaterLog.owner_id == me).delete()
        s.commit()
    client.post("/dashboard/water/log", data={"ounces": "500"})
    with SessionLocal() as s:
        total = sum(w.ounces for w in s.scalars(select(WaterLog).where(WaterLog.owner_id == me)))
        s.query(WaterLog).filter(WaterLog.owner_id == me).delete()
        s.commit()
    assert abs(total - 16.907) < 0.01


def test_metric_workout_log_asks_for_kg_and_kmh(client, db, me, metric):
    from test_workouts import _plan_form
    client.post("/workouts", data=_plan_form(name="Units Plan"), follow_redirects=False)
    with SessionLocal() as s:
        from app.models import WorkoutPlan
        pid = s.scalar(select(WorkoutPlan.id).where(WorkoutPlan.owner_id == me, WorkoutPlan.name == "Units Plan"))
        day_id = s.get(WorkoutPlan, pid).days[0].id
    page = client.get(f"/workouts/day/{day_id}/log").text
    assert "Body weight (kg)" in page
    with SessionLocal() as s:
        s.query(WorkoutPlan).filter(WorkoutPlan.id == pid).delete()
        s.commit()
