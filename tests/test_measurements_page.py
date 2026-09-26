import html
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.measurements.calculations import bmr, macros_for_preset, target_calories, tdee
from app.models import (ActivityLevel, BiologicalSex, BodyMeasurement, DietPreset, MacroGoal,
                         Share, ShareCategory, User)


def _logged_in_client(username: str, password: str = "Other1!") -> TestClient:
    """A second user's own logged-in TestClient (registering already signs them in). Mirrors
    tests/test_vendors_page.py's _logged_in_client."""
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": username, "password": password, "confirm": password})
    return other


def _text(response) -> str:
    return html.unescape(response.text)


def _clear_measurements(*owner_ids: int) -> None:
    with SessionLocal() as s:
        for oid in owner_ids:
            s.query(BodyMeasurement).filter_by(owner_id=oid).delete()
        s.commit()


def test_measurements_page_loads_with_empty_state(client, db, me):
    try:
        r = client.get("/measurements")
        assert r.status_code == 200
        assert "No measurements logged yet" in r.text or "Log a measurement" in r.text
    finally:
        _clear_measurements(me)


def test_new_measurement_entry_all_fields_optional(client, db, me):
    try:
        r = client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "180"},
                        follow_redirects=False)
        assert r.status_code == 303
        with SessionLocal() as s:
            tester = s.scalar(select(User).where(User.username_key == "tester"))
            [bm] = s.scalars(select(BodyMeasurement).where(BodyMeasurement.owner_id == tester.id)).all()
            assert bm.weight_lbs == 180.0 and bm.neck_in is None
    finally:
        _clear_measurements(me)


def test_new_measurement_entry_full_session(client, db, me):
    try:
        r = client.post("/measurements", data={
            "measured_at": "2026-09-28", "weight_lbs": "180", "systolic": "120", "diastolic": "80",
            "neck_in": "15.5", "waist_in": "34", "hips_in": "38",
            "biceps_l_in": "16", "biceps_r_in": "16.3",
            "forearm_l_in": "12", "forearm_r_in": "12.1",
            "quad_l_in": "22", "quad_r_in": "22.2",
            "calf_l_in": "15", "calf_r_in": "15.1",
        }, follow_redirects=False)
        assert r.status_code == 303
        with SessionLocal() as s:
            tester = s.scalar(select(User).where(User.username_key == "tester"))
            [bm] = s.scalars(select(BodyMeasurement).where(BodyMeasurement.owner_id == tester.id)).all()
            assert bm.biceps_l_in == 16.0 and bm.biceps_r_in == 16.3
    finally:
        _clear_measurements(me)


def test_new_measurement_rejects_non_positive_weight(client, db, me):
    try:
        r = client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "-5"})
        assert r.status_code == 422
    finally:
        _clear_measurements(me)


def test_measurements_page_respects_personal_data_sharing(client, db, me):
    other = _logged_in_client("MeasureSharePartner")
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "measuresharepartner"))
    try:
        r = other.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "222.2"},
                       follow_redirects=False)
        assert r.status_code == 303

        # No Share yet -- the signed-in test user must not see the other user's measurement.
        t_before = _text(client.get("/measurements"))
        assert "222.2" not in t_before

        with SessionLocal() as s:
            s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.PERSONAL_DATA))
            s.commit()

        # Now visible, tagged with the owner's name (matching this app's established shared-item
        # display convention, e.g. tests/test_vendors_page.py's "Shared by" column).
        t_after = _text(client.get("/measurements"))
        assert "222.2" in t_after
        assert "MeasureSharePartner" in t_after
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me,
                                     category=ShareCategory.PERSONAL_DATA).delete()
            s.commit()
        _clear_measurements(me, other_id)


def _tester_id() -> int:
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == "tester"))


def _clear_body_profile() -> None:
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.username_key == "tester"))
        u.sex = u.birth_date = u.height_in = u.activity_level = None
        u.macro_goal = u.diet_preset = u.water_goal_oz = None
        u.custom_protein_pct = u.custom_carb_pct = u.custom_fat_pct = None
        s.commit()


def test_silhouette_shows_average_of_bilateral_measurement(client, db):
    tester = _tester_id()
    try:
        client.post("/measurements", data={
            "measured_at": "2026-09-20", "biceps_l_in": "16", "biceps_r_in": "16",
        })
        client.post("/measurements", data={
            "measured_at": "2026-09-27", "biceps_l_in": "16", "biceps_r_in": "16.3",
        })
        t = html.unescape(client.get("/measurements").text)
        assert "16.15" in t  # average of the most recent entry's two sides
    finally:
        _clear_measurements(tester)


def test_silhouette_shows_change_since_previous_entry(client, db):
    tester = _tester_id()
    try:
        client.post("/measurements", data={"measured_at": "2026-09-20", "waist_in": "34"})
        client.post("/measurements", data={"measured_at": "2026-09-27", "waist_in": "33.5"})
        t = html.unescape(client.get("/measurements").text)
        assert "-0.5" in t or "−0.5" in t
    finally:
        _clear_measurements(tester)


def test_silhouette_shows_single_side_when_other_side_missing(client, db):
    """Review Focus item 1: a bilateral measurement missing one side must show that one side
    alone, clearly labeled -- never averaged with None/zero."""
    tester = _tester_id()
    try:
        client.post("/measurements", data={"measured_at": "2026-09-27", "quad_l_in": "22"})
        t = html.unescape(client.get("/measurements").text)
        assert "22.0" in t or "22" in t
        # It must not silently render as if it were a full average (e.g. 11.0 = (22+0)/2).
        assert "11.0" not in t
    finally:
        _clear_measurements(tester)


def test_macros_tab_shows_prompt_when_profile_incomplete(client, db):
    tester = _tester_id()
    try:
        t = client.get("/measurements?tab=macros").text
        assert "profile" in t.lower() or "add your" in t.lower()
    finally:
        _clear_measurements(tester)


def test_macros_tab_computes_from_stored_profile_and_latest_weight(client, db):
    tester = _tester_id()
    try:
        client.post("/settings/body-profile", data={
            "sex": "Male", "birth_date": "1996-01-01", "height_in": "70",
            "activity_level": "1.55", "macro_goal": "0", "diet_preset": "balanced",
        })
        client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "180"})
        r = client.get("/measurements?tab=macros")
        assert r.status_code == 200

        with SessionLocal() as s:
            user = s.scalar(select(User).where(User.username_key == "tester"))
            birth_date = user.birth_date
        today = __import__("datetime").date.today()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))

        bmr_value = bmr(180.0, 70.0, age, BiologicalSex.MALE)
        tdee_value = tdee(bmr_value, ActivityLevel.MODERATELY_ACTIVE)
        calories, floored = target_calories(tdee_value, MacroGoal.MAINTAIN, BiologicalSex.MALE)
        protein_g, carb_g, fat_g = macros_for_preset(calories, DietPreset.BALANCED)

        t = html.unescape(r.text)
        assert str(round(calories)) in t
        assert str(round(protein_g)) in t
        assert str(round(carb_g)) in t
        assert str(round(fat_g)) in t
        if floored:
            assert "adjust" in t.lower()
    finally:
        _clear_measurements(tester)
        _clear_body_profile()


def test_macros_tab_shows_floor_notice_when_calories_floored(client, db):
    """Review Focus item 2: when target_calories() floors the number, the UI must show a
    visible 'adjusted' notice."""
    tester = _tester_id()
    try:
        client.post("/settings/body-profile", data={
            "sex": "Female", "birth_date": "2000-01-01", "height_in": "60",
            "activity_level": "1.2", "macro_goal": "-1000", "diet_preset": "balanced",
        })
        client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "100"})
        r = client.get("/measurements?tab=macros")
        assert r.status_code == 200
        t = html.unescape(r.text)
        assert "adjust" in t.lower()
    finally:
        _clear_measurements(tester)
        _clear_body_profile()


def test_water_goal_and_pace_shown_on_measurements_tab(client, db):
    tester = _tester_id()
    try:
        client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "200"})
        t = client.get("/measurements").text
        assert "100" in t  # default water goal: 200/2
    finally:
        _clear_measurements(tester)


def test_charts_default_to_a_range(client, db, me):
    try:
        r = client.get("/measurements")
        assert r.status_code == 200
    finally:
        _clear_measurements(me)


def test_charts_range_query_param_changes_window(client, db, me):
    old = date(2026, 1, 1)
    recent = date(2026, 9, 28)
    try:
        with SessionLocal() as s:
            tester = s.scalar(select(User).where(User.username_key == "tester"))
            s.add(BodyMeasurement(owner_id=tester.id, measured_at=old, weight_lbs=190))
            s.add(BodyMeasurement(owner_id=tester.id, measured_at=recent, weight_lbs=180))
            s.commit()
        r_lifetime = client.get("/measurements?range=lifetime")
        r_7d = client.get(f"/measurements?range=7d&as_of={recent.isoformat()}")
        assert "190" in r_lifetime.text
        assert "190" not in r_7d.text  # outside the 7-day window from the pinned "as_of" date
    finally:
        _clear_measurements(me)


def test_charts_reject_unknown_range_falls_back_to_default(client, db, me):
    try:
        r = client.get("/measurements?range=bogus")
        assert r.status_code == 200  # never a 500 on a garbage query param
    finally:
        _clear_measurements(me)
