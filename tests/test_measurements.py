import pytest

from app.measurements.calculations import (
    bmi, bmr, body_fat_pct, macros_for_preset, target_calories, tdee, water_goal_oz, water_pace,
)
from app.models import ActivityLevel, BiologicalSex, DietPreset, MacroGoal


def test_bmr_male():
    # 180 lb (~81.65 kg), 70 in (~177.8 cm), age 30, male.
    value = bmr(180, 70, 30, BiologicalSex.MALE)
    assert value == pytest.approx(1782.7, abs=1.0)


def test_bmr_female():
    value = bmr(140, 65, 28, BiologicalSex.FEMALE)
    assert value == pytest.approx(1365.9, abs=1.0)


def test_tdee_multiplies_by_activity_value():
    assert tdee(1800, ActivityLevel.SEDENTARY) == pytest.approx(1800 * 1.2)
    assert tdee(1800, ActivityLevel.EXTRA_ACTIVE) == pytest.approx(1800 * 1.9)


def test_target_calories_applies_goal_offset_without_flooring():
    calories, floored = target_calories(2500, MacroGoal.MODERATE_LOSS, BiologicalSex.MALE)
    assert calories == pytest.approx(2000) and floored is False


def test_target_calories_floors_at_1500_for_male():
    calories, floored = target_calories(1800, MacroGoal.AGGRESSIVE_LOSS, BiologicalSex.MALE)
    assert calories == 1500 and floored is True


def test_target_calories_floors_at_1200_for_female():
    calories, floored = target_calories(1500, MacroGoal.AGGRESSIVE_LOSS, BiologicalSex.FEMALE)
    assert calories == 1200 and floored is True


def test_macros_for_balanced_preset():
    protein, carb, fat = macros_for_preset(2000, DietPreset.BALANCED)
    assert protein == pytest.approx(150) and carb == pytest.approx(200) and fat == pytest.approx(66.7, abs=0.1)


def test_macros_for_keto_preset():
    protein, carb, fat = macros_for_preset(2000, DietPreset.KETO)
    assert protein == pytest.approx(100) and carb == pytest.approx(25) and fat == pytest.approx(166.7, abs=0.1)


def test_macros_for_custom_preset():
    protein, carb, fat = macros_for_preset(2000, DietPreset.CUSTOM, custom=(35, 35, 30))
    assert protein == pytest.approx(175) and carb == pytest.approx(175) and fat == pytest.approx(66.7, abs=0.1)


def test_macros_for_custom_preset_rejects_ratios_not_summing_to_100():
    with pytest.raises(ValueError):
        macros_for_preset(2000, DietPreset.CUSTOM, custom=(35, 35, 20))


def test_macros_for_custom_preset_requires_custom_ratios():
    with pytest.raises(ValueError):
        macros_for_preset(2000, DietPreset.CUSTOM, custom=None)


def test_water_goal_default_is_half_bodyweight():
    assert water_goal_oz(200, None) == 100


def test_water_goal_uses_override_when_set():
    assert water_goal_oz(200, 120) == 120


def test_water_pace_breaks_down_by_hour():
    pace = water_pace(200)
    assert pace["oz_per_hour"] == pytest.approx(12.5)
    assert pace["cups_per_hour"] == pytest.approx(1.5625)
    assert pace["bottles_per_hour"] == pytest.approx(0.7396, abs=0.001)


def test_bmi():
    assert bmi(180, 70) == pytest.approx(25.83, abs=0.01)


def test_body_fat_pct_male():
    value = body_fat_pct(BiologicalSex.MALE, height_in=70, neck_in=15, waist_in=34)
    assert value == pytest.approx(11.05, abs=0.1)


def test_body_fat_pct_female_requires_hips():
    assert body_fat_pct(BiologicalSex.FEMALE, height_in=65, neck_in=13, waist_in=28) is None


def test_body_fat_pct_female_with_hips():
    value = body_fat_pct(BiologicalSex.FEMALE, height_in=65, neck_in=13, waist_in=28, hips_in=38)
    assert value is not None and value == pytest.approx(2.93, abs=0.1)


def test_body_fat_pct_male_returns_none_instead_of_raising_when_waist_not_greater_than_neck():
    # waist == neck -> log10(0) would raise ValueError; must return None instead of crashing.
    assert body_fat_pct(BiologicalSex.MALE, height_in=70, neck_in=15, waist_in=15) is None
    # waist < neck is even worse (negative log10 argument) -- still must not raise.
    assert body_fat_pct(BiologicalSex.MALE, height_in=70, neck_in=15, waist_in=10) is None


def test_body_fat_pct_female_returns_none_instead_of_raising_when_waist_plus_hips_not_greater_than_neck():
    assert body_fat_pct(BiologicalSex.FEMALE, height_in=65, neck_in=40, waist_in=20,
                        hips_in=20) is None


def test_bmi_returns_none_for_missing_or_non_positive_height():
    assert bmi(180, None) is None
    assert bmi(180, 0) is None
    assert bmi(180, -5) is None


def test_the_overview_weight_chart_has_the_lost_or_gained_sash_and_other_metrics_do_not(client, db, me):
    from datetime import timedelta
    from app import clock
    from app.db import SessionLocal
    from app.models import BodyMeasurement, Protocol
    today = clock.today()
    with SessionLocal() as s:
        s.add(Protocol(owner_id=me, name="Sash Protocol", start_date=today - timedelta(days=20)))
        s.add(BodyMeasurement(owner_id=me, measured_at=today - timedelta(days=20), weight_lbs=200.0, neck_in=15.0))
        s.add(BodyMeasurement(owner_id=me, measured_at=today - timedelta(days=1), weight_lbs=195.5, neck_in=15.5))
        s.commit()
    text = client.get("/measurements").text
    panels = text.split('class="overview-chart-panel"')[1:]
    weight = next(p for p in panels if p.startswith(' data-metric="weight_lbs"'))
    assert 'class="weight-ribbon down"' in weight and "Lost 4.5 lbs" in weight
    assert all("weight-ribbon" not in p.split("</svg>")[0] for p in panels if not p.startswith(' data-metric="weight_lbs"'))


RENPHO_HEADER = ("No.,Date,Time,Weight(lb),BMI,Body Fat Percentage(%),Body Fat Mass(lb),Muscle Percentage(%),Muscle Mass(lb),Skeletal Muscle Percentage(%),"
                 "Skeletal Muscle Mass(lb),Bone Percentage(%),Bone Mass(lb),Protein Percentage(%),Protein Mass(lb),Body Water Percentage(%),Body Water Mass(lb),"
                 "Fat-Free Mass(lb),Subcutaneous Fat(%),Visceral Fat,BMR(kcal),Metabolic Age,WHR (Waist-to-Hip Ratio),Optimal Weight(lb),Weight Level,Body Type,"
                 "Target to optimal weight(lb),Target to optimal muscle mass(lb),Target to optimal fat mass(lb),Remarks\n")
RENPHO_ROW = "1,2026.11.09,14:03:58,200.5,30.1,30.0,60.1,60.0,120.3,40.0,80.2,3.0,6.0,14.0,28.1,46.0,92.2,140.4,25.0,12,1800,40,--,--,--,--,--,--,--,--\n"


def test_a_renpho_export_with_the_apps_own_layout_imports(client, db, me):
    from app.db import SessionLocal
    from app.models import BodyMeasurement
    r = client.post("/measurements/import-scale", files={"file": ("export.csv", (RENPHO_HEADER + RENPHO_ROW + "\n").encode(), "text/csv")}, follow_redirects=False)
    assert r.status_code == 303 and "imported=1" in r.headers["location"]
    with SessionLocal() as s:
        [m] = s.query(BodyMeasurement).filter_by(owner_id=me).all()
        assert (m.weight_lbs, m.scale_bmi, m.scale_body_fat_pct, m.scale_visceral_fat) == (200.5, 30.1, 30.0, 12.0)


def test_pasted_csv_text_imports_when_a_file_cannot_be_chosen(client, db, me):
    r = client.post("/measurements/import-scale", data={"text": RENPHO_HEADER + RENPHO_ROW}, follow_redirects=False)
    assert r.status_code == 303 and "imported=1" in r.headers["location"]


def test_an_import_with_no_file_says_what_to_do_instead_of_returning_json(client, db, me):
    for kwargs in ({}, {"files": {"file": ("", b"", "application/octet-stream")}}, {"files": {"file": ("export.csv", b"", "text/csv")}}):
        r = client.post("/measurements/import-scale", **kwargs)
        assert r.status_code == 422 and "No file came through" in r.text and not r.text.lstrip().startswith("{")


def _entry(me, **values):
    from app import clock
    from app.db import SessionLocal
    from app.models import BodyMeasurement
    with SessionLocal() as s:
        row = BodyMeasurement(owner_id=me, measured_at=clock.today(), **values)
        s.add(row)
        s.commit()
        return row.id


def test_the_edit_button_opens_the_form_filled_in_and_saving_changes_the_entry(client, db, me):
    from app.db import SessionLocal
    from app.models import BodyMeasurement
    entry = _entry(me, weight_lbs=200.0, hips_in=0.4925, waist_in=40.0, notes={"hips_in": "after dinner"})
    page = client.get("/measurements").text
    assert f"/measurements?tab=measurements&amp;edit_entry={entry}" in page or f"edit_entry={entry}" in page
    form = client.get(f"/measurements?edit_entry={entry}").text
    assert f'action="/measurements/{entry}/edit"' in form and "Edit Measurement" in form and 'value="0.4925"' in form and "Save Changes" in form
    r = client.post(f"/measurements/{entry}/edit", data={"measured_at": "2026-11-08", "weight_lbs": "199.5", "hips_in": "44.5", "waist_in": "", "note__hips_in": "fixed"},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        m = s.get(BodyMeasurement, entry)
        assert (m.weight_lbs, m.hips_in, m.waist_in, str(m.measured_at), m.notes) == (199.5, 44.5, None, "2026-11-08", {"hips_in": "fixed"})
        assert s.query(BodyMeasurement).filter_by(owner_id=me).count() == 1                 # edited in place, not added


def test_a_bad_edit_shows_the_error_and_keeps_the_entry(client, db, me):
    from app.db import SessionLocal
    from app.models import BodyMeasurement
    entry = _entry(me, weight_lbs=200.0)
    r = client.post(f"/measurements/{entry}/edit", data={"measured_at": "2026-11-08", "weight_lbs": "abc"})
    assert r.status_code == 422 and "Enter a number." in r.text and f'action="/measurements/{entry}/edit"' in r.text
    with SessionLocal() as s:
        assert s.get(BodyMeasurement, entry).weight_lbs == 200.0


def test_nobody_can_edit_someone_elses_entry(client, db, me):
    from app.db import SessionLocal
    from app.models import BodyMeasurement, User
    with SessionLocal() as s:
        other = User(username="otherperson", username_key="otherperson", password_hash="x")
        s.add(other)
        s.commit()
        row = BodyMeasurement(owner_id=other.id, measured_at=__import__("app.clock", fromlist=["x"]).today(), weight_lbs=150.0)
        s.add(row)
        s.commit()
        rid = row.id
    assert client.get(f"/measurements?edit_entry={rid}").status_code == 404
    assert client.post(f"/measurements/{rid}/edit", data={"weight_lbs": "1"}).status_code == 404


def test_a_second_entry_within_12_hours_keeps_the_original_and_fills_its_blanks(client, db, me):
    from app import clock
    from app.db import SessionLocal
    from app.models import BodyMeasurement
    first = client.post("/measurements", data={"measured_at": clock.today().isoformat(), "weight_lbs": "200", "waist_in": "40"}, follow_redirects=False)
    assert first.status_code == 303
    r = client.post("/measurements", data={"measured_at": clock.today().isoformat(), "weight_lbs": "150", "hips_in": "44", "waist_in": "10"}, follow_redirects=False)
    assert r.status_code == 303 and "kept=" in r.headers["location"] and "filled=1" in r.headers["location"]
    with SessionLocal() as s:
        [m] = s.query(BodyMeasurement).filter_by(owner_id=me).all()
        assert (m.weight_lbs, m.waist_in, m.hips_in) == (200.0, 40.0, 44.0)          # the original's numbers stand; the blank hips box was filled
    page = client.get(r.headers["location"]).text
    assert "within 12 hours" in page and "1 blank value" in page


def test_an_entry_more_than_12_hours_later_is_a_new_entry(client, db, me):
    from datetime import datetime, timedelta
    from app import clock
    from app.db import SessionLocal
    from app.models import BodyMeasurement
    with SessionLocal() as s:                                                                 # taken 13 hours ago
        s.add(BodyMeasurement(owner_id=me, measured_at=clock.today(), weight_lbs=200.0, recorded_at=clock.now().replace(tzinfo=None) - timedelta(hours=13)))
        s.commit()
    client.post("/measurements", data={"measured_at": clock.today().isoformat(), "weight_lbs": "199"})
    with SessionLocal() as s:
        assert s.query(BodyMeasurement).filter_by(owner_id=me).count() == 2


def test_a_scale_reading_within_12_hours_of_a_typed_entry_adds_its_numbers_to_it(client, db, me):
    from app import clock
    from app.db import SessionLocal
    from app.models import BodyMeasurement
    with SessionLocal() as s:
        s.add(BodyMeasurement(owner_id=me, measured_at=__import__("datetime").date(2026, 11, 9), weight_lbs=268.6, hips_in=44.0))
        s.commit()
    r = client.post("/measurements/import-scale", data={"text": RENPHO_HEADER + RENPHO_ROW}, follow_redirects=False)
    assert r.status_code == 303 and "imported=0" in r.headers["location"] and "merged=1" in r.headers["location"]
    with SessionLocal() as s:
        [m] = s.query(BodyMeasurement).filter_by(owner_id=me).all()
        assert (m.weight_lbs, m.hips_in, m.scale_body_fat_pct, m.scale_bmi) == (268.6, 44.0, 30.0, 30.1)     # typed numbers kept; the scale's added
        assert m.scale_stamp == "2026-11-09 14:03:58"
    again = client.post("/measurements/import-scale", data={"text": RENPHO_HEADER + RENPHO_ROW}, follow_redirects=False)
    assert "imported=0" in again.headers["location"] and "merged=0" in again.headers["location"]            # importing the file again changes nothing
    with SessionLocal() as s:
        assert s.query(BodyMeasurement).filter_by(owner_id=me).count() == 1


def test_two_scale_readings_within_12_hours_make_one_entry(client, db, me):
    from app.db import SessionLocal
    from app.models import BodyMeasurement
    later = RENPHO_ROW.replace("14:03:58", "18:30:00").replace("200.5", "201.0").replace("1,2026", "2,2026")
    r = client.post("/measurements/import-scale", data={"text": RENPHO_HEADER + RENPHO_ROW + later}, follow_redirects=False)
    assert "imported=1" in r.headers["location"]
    with SessionLocal() as s:
        [m] = s.query(BodyMeasurement).filter_by(owner_id=me).all()
        assert m.weight_lbs == 200.5                                                         # the earlier reading is the one used
