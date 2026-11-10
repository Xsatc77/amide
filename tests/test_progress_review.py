from datetime import date, datetime, timedelta

from app.db import SessionLocal
from app.models import (BodyMeasurement, DoseLog, DoseStatus, DoseUnit, FoodLog, JournalEntry, LabMarker, LabPanel,
                        LabResult, TimeOfDay, WaterLog, WorkoutLog)
from app.progress import review

START = date(2030, 1, 1)           # invented dates and numbers throughout


def _weigh(s, me, day, **v):
    s.add(BodyMeasurement(owner_id=me, measured_at=day, recorded_at=datetime.combine(day, datetime.min.time()), **v))


def _build(me, **kw):
    with SessionLocal() as s:
        from app.models import User
        return review.build(s, s.get(User, me), START, START + timedelta(days=28), **kw)


def _seed_losing_fast(me):
    with SessionLocal() as s:
        for week, weight in enumerate([200.0, 195.0, 190.0, 189.0, 188.0]):
            _weigh(s, me, START + timedelta(days=7 * week), weight_lbs=weight, waist_in=40.0 - week,
                   scale_fat_mass_lb=60.0 - 3 * week, scale_muscle_mass_lb=120.0 - week, systolic=120 - 5 * week, diastolic=80)
        s.commit()


def test_body_rows_show_first_last_and_change(client, db, me):
    _seed_losing_fast(me)
    r = _build(me)
    weight = next(row for row in r["body"] if row["key"] == "weight_lbs")
    assert (weight["first"], weight["last"], weight["change"]) == (200.0, 188.0, -12.0)
    assert round(weight["pct"], 1) == -6.0


def test_the_fast_loss_flag_names_the_weeks_over_the_limit(client, db, me):
    _seed_losing_fast(me)
    flags = _build(me)["flags"]
    loss = next(f for f in flags if f["key"] == "fast_loss")
    assert "2 of 4" in loss["text"] and "1.0%" in loss["rule"]


def test_muscle_down_while_fat_down_is_flagged(client, db, me):
    _seed_losing_fast(me)
    assert any(f["key"] == "muscle_loss" for f in _build(me)["flags"])


def test_blood_pressure_is_compared_with_the_persons_own_start_not_a_fixed_number(client, db, me):
    _seed_losing_fast(me)                                        # 120 down to 100: an own-baseline drop, never below a fixed floor
    flags = _build(me)["flags"]
    assert any(f["key"] == "bp_drop" for f in flags)
    assert not any("100" in f["rule"] for f in flags if f["key"] == "bp_drop")


def test_adherence_and_intake_numbers(client, db, me):
    with SessionLocal() as s:
        s.add(WorkoutLog(owner_id=me, log_date=START + timedelta(days=1)))
        s.add(WorkoutLog(owner_id=me, log_date=START + timedelta(days=3), plan_day_id=None))
        for d in (0, 1):
            s.add(FoodLog(owner_id=me, eaten_on=START + timedelta(days=d), meal="lunch", name="x", serving="1", servings=1,
                          calories=1000, protein_g=100, carb_g=30, fat_g=40, fiber_g=10))
            s.add(WaterLog(owner_id=me, logged_at=START + timedelta(days=d), ounces=64))
        s.add(JournalEntry(owner_id=me, entry_date=START, sleep_quality=1))
        s.add(JournalEntry(owner_id=me, entry_date=START + timedelta(days=1), sleep_quality=4))
        s.commit()
    r = _build(me)
    assert r["adherence"]["workouts"] == 2
    assert r["adherence"]["food_days"] == 2 and r["adherence"]["days"] == 29
    assert r["intake"]["calories"] == 1000 and r["intake"]["protein_g"] == 100 and r["intake"]["water_oz"] == 64
    assert r["journal"]["sleep_low_days"] == 1 and r["journal"]["rated_days"] == 2


def test_doses_are_counted_by_outcome(client, db, me):
    from app.models import Peptide, Protocol
    with SessionLocal() as s:
        pep = Peptide(name="Testpep")
        s.add(pep)
        proto = Protocol(owner_id=me, name="P", start_date=START)
        s.add(proto)
        s.flush()
        for status in (DoseStatus.ON_TIME, DoseStatus.LATE, DoseStatus.SKIPPED):
            s.add(DoseLog(owner_id=me, protocol_id=proto.id, peptide_id=pep.id, peptide_name="Testpep", dose_value=1, dose_unit=list(DoseUnit)[0],
                          route="subq", scheduled_date=START + timedelta(days=2), scheduled_time_of_day=list(TimeOfDay)[0], status=status))
        s.commit()
    d = _build(me)["adherence"]
    assert (d["doses_logged"], d["doses_skipped"]) == (2, 1)


def test_labs_drawn_in_the_period_are_listed_out_of_range_first_and_none_is_flagged(client, db, me):
    assert any(f["key"] == "no_labs" for f in _build(me)["flags"])
    with SessionLocal() as s:
        panel = LabPanel(owner_id=me, drawn_at=START + timedelta(days=5))
        panel.results = [LabResult(marker=LabMarker.FERRITIN, value=5, unit="ng/mL", range_low=30, range_high=300),
                         LabResult(marker=LabMarker.HBA1C, value=5.0, unit="%", range_low=4, range_high=5.6)]
        s.add(panel)
        s.commit()
    r = _build(me)
    assert not any(f["key"] == "no_labs" for f in r["flags"])
    assert [x["marker"] for x in r["labs"]] == ["Ferritin", "HbA1c"] and r["labs"][0]["out_of_range"] is True


def test_the_page_renders_prints_and_takes_a_date_range(client, db, me):
    _seed_losing_fast(me)
    page = client.get(f"/progress?start={START.isoformat()}&end={(START + timedelta(days=28)).isoformat()}")
    assert page.status_code == 200 and "Progress Review" in page.text and "data-print" in page.text
    assert "Fast loss" in page.text or "fast" in page.text.lower()
    assert client.get("/progress").status_code == 200                       # defaults work with no data
    assert client.get("/progress?start=nonsense").status_code == 200        # a bad date falls back rather than failing
