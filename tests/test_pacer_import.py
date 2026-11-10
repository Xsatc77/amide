import io
import zipfile
from datetime import date, datetime

from app.db import SessionLocal
from app.measurements import pacer
from app.models import BodyMeasurement

# Invented readings only.
WEIGHT = "Time,Weight,Unit,Comments\n2020-03-02 07:10:00,210.0,lbs,\n2020-03-09 08:30:00,208.4,lbs,Check\nnot-a-date,200,lbs,\n"
HEART = "Time,Systolic,Diastolic,HeartRate,Comments\n2020-03-02 07:11:00,118.0,76.0,64.0,\n2020-03-09 08:29:00,121.0,79.0,0.0,\n"
BOTH = WEIGHT + "\n---FILE---\n" + HEART


def _all(me):
    with SessionLocal() as s:
        return s.query(BodyMeasurement).filter_by(owner_id=me).order_by(BodyMeasurement.measured_at).all()


def test_weight_and_heart_logs_are_read_and_a_zero_pulse_means_none():
    w = pacer.parse_weight(WEIGHT)
    h = pacer.parse_heart(HEART)
    assert [r.values["weight_lbs"] for r in w.readings] == [210.0, 208.4]
    assert len(w.errors) == 1
    assert h.readings[0].values == {"systolic": 118, "diastolic": 76, "heart_rate_bpm": 64}
    assert "heart_rate_bpm" not in h.readings[1].values


def test_kilograms_are_converted_to_pounds():
    [r] = pacer.parse_weight("Time,Weight,Unit,Comments\n2020-03-02 07:10:00,100.0,kg,\n").readings
    assert r.values["weight_lbs"] == 220.5


def test_a_weigh_in_and_its_blood_pressure_become_one_entry_and_a_second_import_adds_nothing(client, db, me):
    r = client.post("/measurements/import-pacer", data={"text": BOTH}, follow_redirects=False)
    assert r.status_code == 303 and "imported=2" in r.headers["location"] and "merged=2" in r.headers["location"]
    assert [(x.weight_lbs, x.systolic, x.heart_rate_bpm) for x in _all(me)] == [(210.0, 118, 64), (208.4, 121, None)]
    again = client.post("/measurements/import-pacer", data={"text": BOTH}, follow_redirects=False)
    assert "imported=0" in again.headers["location"]
    assert len(_all(me)) == 2


def test_an_existing_entry_keeps_its_own_numbers(client, db, me):
    with SessionLocal() as s:
        s.add(BodyMeasurement(owner_id=me, measured_at=date(2020, 3, 2), recorded_at=datetime(2020, 3, 2, 9), weight_lbs=211.5))
        s.commit()
    client.post("/measurements/import-pacer", data={"text": BOTH})
    first = _all(me)[0]
    assert (first.weight_lbs, first.systolic) == (211.5, 118)


def test_the_exported_zip_is_read_and_the_steps_files_are_ignored(client, db, me):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("WeightLogs_x.csv", WEIGHT)
        z.writestr("HeartLogs_x.csv", HEART)
        z.writestr("DetailedSteps_x.csv", "StartTime,EndTime\n" * 10)
    r = client.post("/measurements/import-pacer", files={"files": ("PacerDataLogs.zip", buf.getvalue(), "application/zip")}, follow_redirects=False)
    assert r.status_code == 303 and "imported=2" in r.headers["location"]
    assert len(_all(me)) == 2


def test_a_file_that_is_not_a_pacer_export_is_refused_and_saves_nothing(client, db, me):
    r = client.post("/measurements/import-pacer", data={"text": "a,b\n1,2\n"})
    assert r.status_code == 422 and "Pacer" in r.text
    assert _all(me) == []
