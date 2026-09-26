import html

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import BodyMeasurement, Share, ShareCategory, User


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
