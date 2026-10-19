"""Typing a scale's numbers in by hand, and deleting a reading. Invented numbers."""

import re
from datetime import date

import pytest
from sqlalchemy import select

from app.measurements.scale_view import SCALE_FIELDS
from app.models import BodyMeasurement, User


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(BodyMeasurement).delete()
    db.get(User, me).units = "us"
    db.commit()


def log(client, **fields):
    return client.post("/measurements", data={"measured_at": "2026-10-19", "weight_lbs": "200", **fields}, follow_redirects=False)


def test_the_log_dialog_has_a_box_for_every_scale_number(client):
    page = client.get("/measurements?tab=measurements").text
    dialog = page.split('id="measurement-dialog"', 1)[1].split("</dialog>", 1)[0]
    for field, _, _ in SCALE_FIELDS:
        assert f'name="{field}"' in dialog, field


def test_the_scale_numbers_typed_in_are_stored_with_the_entry(client, db, me):
    r = log(client, scale_body_fat_pct="25.5", scale_muscle_mass_lb="140", scale_visceral_fat="9", scale_bmr_kcal="1800")
    assert r.status_code == 303
    row = db.scalar(select(BodyMeasurement).where(BodyMeasurement.owner_id == me))
    assert (row.scale_body_fat_pct, row.scale_muscle_mass_lb, row.scale_visceral_fat, row.scale_bmr_kcal) == (25.5, 140.0, 9.0, 1800.0)
    assert row.scale_stamp is None and row.scale_water_pct is None


def test_a_metric_user_types_masses_in_kilograms_and_they_are_stored_in_pounds(client, db, me):
    db.get(User, me).units = "metric"
    db.commit()
    log(client, weight_lbs="90", scale_muscle_mass_lb="63.5", scale_body_fat_pct="25")
    row = db.scalar(select(BodyMeasurement).where(BodyMeasurement.owner_id == me))
    assert row.scale_muscle_mass_lb == pytest.approx(140.0, abs=0.2) and row.scale_body_fat_pct == 25.0


@pytest.mark.parametrize("field,value", [("scale_body_fat_pct", "abc"), ("scale_body_fat_pct", "120"), ("scale_water_pct", "-5"), ("scale_bmr_kcal", "0")])
def test_a_bad_scale_number_is_refused_and_nothing_is_stored(client, db, me, field, value):
    r = log(client, **{field: value})
    assert r.status_code == 422
    assert db.query(BodyMeasurement).count() == 0


def test_the_entries_table_shows_the_scale_numbers_and_each_row_can_be_deleted(client, db, me):
    log(client, scale_body_fat_pct="25.5", scale_muscle_mass_lb="140")
    row = db.scalar(select(BodyMeasurement).where(BodyMeasurement.owner_id == me))
    page = client.get("/measurements?tab=measurements&range=1yr").text
    table = page.split('id="entries-heading"', 1)[1]
    assert "25.5" in table and "140" in table and f'/measurements/{row.id}/delete' in table


def test_the_latest_scale_card_can_delete_that_reading(client, db, me):
    log(client, scale_body_fat_pct="25.5")
    row = db.scalar(select(BodyMeasurement).where(BodyMeasurement.owner_id == me))
    card = client.get("/measurements?tab=measurements&range=1yr").text.split('id="scale-latest"', 1)[1].split("</section>", 1)[0]
    assert re.search(rf'action="/measurements/{row.id}/delete"', card)
    assert client.post(f"/measurements/{row.id}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(BodyMeasurement).count() == 0
