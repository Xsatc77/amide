"""Importing a smart scale's export (Renpho CSV): every reading becomes a measurement with the scale's numbers kept as their own fields. Invented numbers."""

import io
import re
from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.measurements import renpho
from app.models import BodyMeasurement, User

HEADER = ("No.,Date,Time,Weight(lb),BMI,Body Fat Percentage(%),Body Fat Mass(lb),Muscle Percentage(%),Muscle Mass(lb),Skeletal Muscle Percentage(%),"
          "Skeletal Muscle Mass(lb),Bone Percentage(%),Bone Mass(lb),Protein Percentage(%),Protein Mass(lb),Body Water Percentage(%),Body Water Mass(lb),"
          "Fat-Free Mass(lb),Subcutaneous Fat(%),Visceral Fat,BMR(kcal),Metabolic Age,WHR (Waist-to-Hip Ratio),Optimal Weight(lb),Weight Level,Body Type,"
          "Target to optimal weight(lb),Target to optimal muscle mass(lb),Target to optimal fat mass(lb),Remarks")
FULL = "1,2026.10.19,11:37:37,200.0,30.1,25.5,51.0,70.0,140.0,40.0,80.0,3.2,6.4,15.0,30.0,50.0,100.0,149.0,20.5,9,1800,41,0.9,--,--,--,--,--,--,--"
WEIGHT_ONLY = "2,2026.10.19,11:34:16,200.4,30.2,--,--,--,--,--,--,--,--,--,--,--,--,--,--,--,--,--,--,--,--,--,--,--,--,--"
EARLIER = "3,2026.10.12,07:05:00,203.0,30.6,26.1,53.0,69.4,141.0,39.8,80.8,3.1,6.3,14.9,30.2,49.6,100.7,150.0,21.0,10,1790,42,--,--,--,--,--,--,--,--"


def csv(*rows, header=HEADER):
    return "﻿" + "\r\n".join([header, *rows]) + "\r\n"


# ---- the reader ---------------------------------------------------------------------------------------------------------------------------

def test_a_full_row_gives_every_value_and_the_exact_time():
    (reading,) = renpho.parse(csv(FULL)).readings
    assert reading.stamp == datetime(2026, 10, 19, 11, 37, 37)
    v = reading.values
    assert v["weight_lbs"] == 200.0 and v["scale_bmi"] == 30.1 and v["scale_body_fat_pct"] == 25.5 and v["scale_fat_mass_lb"] == 51.0
    assert v["scale_muscle_pct"] == 70.0 and v["scale_muscle_mass_lb"] == 140.0 and v["scale_skeletal_muscle_pct"] == 40.0 and v["scale_skeletal_muscle_mass_lb"] == 80.0
    assert v["scale_bone_pct"] == 3.2 and v["scale_bone_mass_lb"] == 6.4 and v["scale_protein_pct"] == 15.0 and v["scale_protein_mass_lb"] == 30.0
    assert v["scale_water_pct"] == 50.0 and v["scale_water_mass_lb"] == 100.0 and v["scale_fat_free_mass_lb"] == 149.0 and v["scale_subcutaneous_fat_pct"] == 20.5
    assert v["scale_visceral_fat"] == 9 and v["scale_bmr_kcal"] == 1800 and v["scale_metabolic_age"] == 41 and v["scale_whr"] == 0.9


def test_dashes_mean_no_value_and_a_weight_only_row_is_still_a_weigh_in():
    (reading,) = renpho.parse(csv(WEIGHT_ONLY)).readings
    assert reading.values == {"weight_lbs": 200.4, "scale_bmi": 30.2}


def test_rows_come_back_oldest_first_so_later_readings_get_later_ids():
    result = renpho.parse(csv(FULL, WEIGHT_ONLY, EARLIER))
    assert [r.stamp for r in result.readings] == [datetime(2026, 10, 12, 7, 5), datetime(2026, 10, 19, 11, 34, 16), datetime(2026, 10, 19, 11, 37, 37)]


def test_a_kilogram_export_is_stored_in_pounds():
    header = HEADER.replace("(lb)", "(kg)")
    (reading,) = renpho.parse(csv("1,2026.10.19,11:37:37,90.0,30.1,25.5,23.0,70,63.0,40,36.0,3.2,3.0,15,13.5,50,45.0,67.0,20.5,9,1800,41,0.9,--,--,--,--,--,--,--", header=header)).readings
    assert reading.values["weight_lbs"] == pytest.approx(198.4, abs=0.1) and reading.values["scale_fat_mass_lb"] == pytest.approx(50.7, abs=0.1)
    assert reading.values["scale_body_fat_pct"] == 25.5 and reading.values["scale_bmr_kcal"] == 1800        # percentages and kcal are not converted


def test_a_bad_row_is_reported_and_the_good_ones_still_read():
    result = renpho.parse(csv(FULL, "9,not-a-date,11:00:00,200,30", EARLIER))
    assert len(result.readings) == 2 and len(result.errors) == 1 and "9" in result.errors[0]


def test_a_file_that_is_not_a_renpho_export_is_refused():
    with pytest.raises(renpho.ImportError_):
        renpho.parse("name,amount\nrent,100\n")
    with pytest.raises(renpho.ImportError_):
        renpho.parse("")


# ---- importing ----------------------------------------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(BodyMeasurement).delete()
    db.get(User, me).height_in = None
    db.commit()


def upload(client, text):
    return client.post("/measurements/import-scale", files={"file": ("scale.csv", io.BytesIO(text.encode("utf-8")), "text/csv")}, follow_redirects=False)


def test_importing_makes_a_measurement_per_reading_on_its_date(client, db, me):
    r = upload(client, csv(FULL, WEIGHT_ONLY, EARLIER))
    assert r.status_code == 303 and "imported=3" in r.headers["location"]
    rows = db.scalars(select(BodyMeasurement).where(BodyMeasurement.owner_id == me).order_by(BodyMeasurement.id)).all()
    assert [(m.measured_at, m.weight_lbs) for m in rows] == [(date(2026, 10, 12), 203.0), (date(2026, 10, 19), 200.4), (date(2026, 10, 19), 200.0)]
    assert rows[2].scale_body_fat_pct == 25.5 and rows[2].scale_visceral_fat == 9 and rows[2].scale_stamp == "2026-10-19 11:37:37"
    assert rows[1].scale_body_fat_pct is None


def test_importing_the_same_file_again_adds_nothing(client, db, me):
    upload(client, csv(FULL, EARLIER))
    r = upload(client, csv(FULL, EARLIER, WEIGHT_ONLY))
    assert "imported=1" in r.headers["location"] and "skipped=2" in r.headers["location"]
    assert db.query(BodyMeasurement).count() == 3


def test_a_bad_file_changes_nothing_and_says_why(client, db, me):
    r = upload(client, "name,amount\nrent,100\n")
    assert r.status_code == 422 and "Renpho" in r.text
    assert db.query(BodyMeasurement).count() == 0


def test_the_scale_numbers_are_choices_in_the_overview_dropdown_and_a_latest_reading_card_shows_them(client, db, me):
    upload(client, csv(FULL, EARLIER))
    page = client.get("/measurements?tab=measurements&range=1yr").text
    for field in ("scale_body_fat_pct", "scale_muscle_mass_lb", "scale_visceral_fat", "scale_bmr_kcal"):
        assert f'<option value="{field}">' in page and f'data-metric="{field}"' in page, field
    card = page.split('id="scale-latest"', 1)[1][:4000]
    assert "25.5" in card and "Visceral" in card and "10/19/2026" in card


def test_a_metric_user_sees_scale_masses_in_kilograms(client, db, me):
    upload(client, csv(FULL))
    user = db.get(User, me)
    previous = user.units
    user.units = "metric"
    db.commit()
    try:
        card = client.get("/measurements?tab=measurements&range=1yr").text.split('id="scale-latest"', 1)[1][:4000]
        assert "63.5" in card and "kg" in card            # 140 lb of muscle
    finally:
        user.units = previous
        db.commit()
