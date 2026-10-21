"""Every reading, workout and symptom counts on the phone's own local day, however often the phone sends, overlaps or travels."""

import gzip
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app import config
from app.health import history, parse, series
from app.models import HealthEvent, HealthMetricChoice, HealthRaw, HealthReading, naive_utcnow


def post(db, me, rows, aggregation=None, metric="step_count", source="Phone"):
    """Imports one invented export of `rows` ((date text, qty) pairs) as the receiver would."""
    body = json.dumps({"metrics": [{"name": metric, "units": "count", "data": [{"qty": q, "date": d, "source": source} for d, q in rows]}]}).encode()
    config.HEALTH_RAW_DIR.mkdir(parents=True, exist_ok=True)
    name = f"ld-{db.query(HealthRaw).count()}.json.gz"
    (config.HEALTH_RAW_DIR / name).write_bytes(gzip.compress(body))
    raw = HealthRaw(owner_id=me, sha256=name, size_bytes=len(body), file_name=name,
                    headers={"automation-aggregation": aggregation} if aggregation else None)
    db.add(raw)
    db.commit()
    parse.import_raw(db, raw)
    return raw


def days(db, me, metric="step_count"):
    return series.daily_series(db, me, metric, date(2026, 10, 1), date(2026, 10, 31), "UTC")


def test_three_hourly_posts_in_one_day_overlap_without_duplicates(db, me):
    post(db, me, [("2026-10-20 08:00:00 -0500", 400), ("2026-10-20 09:00:00 -0500", 100)], "hours")       # 09:00 still under way
    post(db, me, [("2026-10-20 09:00:00 -0500", 350), ("2026-10-20 10:00:00 -0500", 50)], "hours")        # since last sync, overlapping
    post(db, me, [("2026-10-20 10:00:00 -0500", 600), ("2026-10-20 11:00:00 -0500", 20), ("2026-10-20 23:00:00 -0500", 5)], "hours")
    assert db.query(HealthReading).count() == 5
    assert days(db, me) == [(date(2026, 10, 20), 400 + 350 + 600 + 20 + 5.0)]        # 23:00 -0500 is 04:00 UTC on the 21st, still the 20th


def test_three_daily_posts_in_one_day_keep_one_growing_total(db, me):
    for total in (1200, 5400, 8100):                        # a partial day, then the same day re-sent twice as it fills up
        post(db, me, [("2026-10-20 00:00:00 -0500", total)], "days")
    assert db.query(HealthReading).count() == 1
    assert days(db, me) == [(date(2026, 10, 20), 8100.0)]


def test_a_2330_minus_0800_and_a_0010_utc_reading_land_on_their_own_days(db, me):
    post(db, me, [("2026-10-20 23:30:00 -0800", 70), ("2026-10-21 00:10:00 +0000", 30)], "hours")
    # 23:30 -0800 is 07:30 UTC on the 21st, later than the other reading in UTC, yet it is the 20th where the phone was
    assert days(db, me) == [(date(2026, 10, 20), 70.0), (date(2026, 10, 21), 30.0)]
    for zone in ("Pacific/Kiritimati", "Etc/GMT+12", None):     # the viewer's zone moves neither
        assert series.daily_series(db, me, "step_count", date(2026, 10, 1), date(2026, 10, 31), zone) == days(db, me)


def test_a_traveller_resending_the_same_day_from_another_zone_is_stored_once(db, me):
    post(db, me, [("2026-10-20 00:00:00 -0500", 6000)], "days")
    post(db, me, [("2026-10-20 00:00:00 +0100", 6500)], "days")      # the same local day, sent again after the flight
    rows = db.query(HealthReading).all()
    assert len(rows) == 1
    assert (rows[0].value, rows[0].offset_minutes, rows[0].start_at) == (6500, 60, datetime(2026, 10, 19, 23))      # moved to the new offset
    assert days(db, me) == [(date(2026, 10, 20), 6500.0)]
    out = history.history(db, me, datetime(2026, 10, 1), datetime(2026, 11, 1))
    assert [(r["local_date"], r["value"]) for r in out] == [("2026-10-20", 6500.0)]


def test_a_traveller_keeps_both_neighbouring_days(db, me):
    post(db, me, [("2026-10-19 00:00:00 -0500", 4000), ("2026-10-20 00:00:00 -0500", 100)], "days")
    post(db, me, [("2026-10-20 00:00:00 +0100", 7000), ("2026-10-21 00:00:00 +0100", 300)], "days")
    assert days(db, me) == [(date(2026, 10, 19), 4000.0), (date(2026, 10, 20), 7000.0), (date(2026, 10, 21), 300.0)]
    assert db.query(HealthReading).count() == 3


def test_history_gives_one_row_per_local_day_even_for_rows_stored_twice_before(db, me):
    # two day rows for the same local day, as an older version could store them after a trip
    for start, offset, value in ((datetime(2026, 10, 20, 5), -300, 6000.0), (datetime(2026, 10, 19, 23), 60, 6500.0)):
        db.add(HealthReading(owner_id=me, metric="step_count", start_at=start, end_at=start + timedelta(days=1), grain="day", value=value,
                             unit="count", source="Phone", offset_minutes=offset))
        db.commit()
    out = history.history(db, me, datetime(2026, 10, 1), datetime(2026, 11, 1))
    assert [(r["local_date"], r["value"]) for r in out] == [("2026-10-20", 6500.0)]           # the later one stands
    assert days(db, me) == [(date(2026, 10, 20), 6500.0)]


def test_a_late_workout_and_symptom_show_on_the_phone_day_for_a_viewer_ten_hours_ahead(db, me):
    payload = {"workouts": [{"id": "W-9", "name": "Zephyr Walk", "start": "2026-10-20 23:30:00 -0800", "end": "2026-10-20 23:59:00 -0800"}],
               "symptoms": [{"id": "S-9", "name": "Headache", "start": "2026-10-20 23:30:00 -0800"}],
               "state_of_mind": [{"id": "M-9", "name": "Calm", "date": "2026-10-20 23:45:00 -0800"}]}
    body = json.dumps(payload).encode()
    config.HEALTH_RAW_DIR.mkdir(parents=True, exist_ok=True)
    (config.HEALTH_RAW_DIR / "ev.json.gz").write_bytes(gzip.compress(body))
    raw = HealthRaw(owner_id=me, sha256="ev", size_bytes=len(body), file_name="ev.json.gz")
    db.add(raw)
    db.commit()
    parse.import_raw(db, raw)
    db.query(HealthMetricChoice).delete()                          # the import left them pending; switch them on
    db.add_all([HealthMetricChoice(owner_id=me, metric=m, enabled=True) for m in ("event:symptom", "event:state_of_mind")])
    db.commit()
    data = series.apple_tab_data(db, me, "lifetime", "Etc/GMT-10")                # UTC+10: 07:30 UTC on the 21st is 17:30 on the 21st there
    assert [w["day"] for w in data["workouts"]] == [date(2026, 10, 20)]
    assert [s["day"] for s in data["symptoms"]] == [date(2026, 10, 20)]
    assert [s["day"] for s in data["state_of_mind"]] == [date(2026, 10, 20)]


def test_an_event_without_an_offset_falls_back_to_the_viewer_zone(db, me):
    db.add(HealthEvent(owner_id=me, kind="workout", external_id="n", start_at=datetime(2026, 10, 20, 20), name="Borealis Ride",
                       payload={"start": "2026-10-20T20:00:00"}))
    db.add(HealthEvent(owner_id=me, kind="workout", external_id="d", start_at=datetime(2026, 10, 18), name="Day Only",
                       payload={"start": "2026-10-18"}))                         # a bare date is already the phone's day
    db.commit()
    data = series.apple_tab_data(db, me, "lifetime", "Etc/GMT-10")
    assert {w["name"]: w["day"] for w in data["workouts"]} == {"Borealis Ride": date(2026, 10, 21), "Day Only": date(2026, 10, 18)}
    behind = series.apple_tab_data(db, me, "lifetime", "Etc/GMT+5")             # UTC-5: midnight UTC would be the 17th there
    assert {w["name"]: w["day"] for w in behind["workouts"]} == {"Borealis Ride": date(2026, 10, 20), "Day Only": date(2026, 10, 18)}


@pytest.mark.parametrize("zone, hours", [("Etc/GMT-14", 14), ("Etc/GMT+12", -12)])
def test_today_and_the_week_lose_or_double_nothing_at_their_edges(db, me, zone, hours):
    """A viewer 14 hours ahead of UTC and one 12 hours behind, whose phone is in the same zone."""
    today = (datetime.now(timezone.utc) + timedelta(hours=hours)).date()
    offset = hours * 60

    def at(day, hh, mm, value):
        local = datetime.combine(day, datetime.min.time()) + timedelta(hours=hh, minutes=mm)
        start = local - timedelta(minutes=offset)
        db.add(HealthReading(owner_id=me, metric="step_count", start_at=start, end_at=start + timedelta(hours=1), grain="hour", value=value,
                             unit="count", source="Phone", offset_minutes=offset))

    at(today, 0, 5, 1)            # the first minutes of today
    at(today, 23, 55, 2)          # the last minutes of today
    at(today - timedelta(days=1), 23, 55, 4)          # just before today
    at(today - timedelta(days=6), 0, 5, 8)            # the oldest day of the week
    at(today - timedelta(days=7), 23, 55, 16)         # just outside the week
    at(today - timedelta(days=7), 0, 5, 32)           # the first day of the 7-day range
    at(today - timedelta(days=8), 23, 55, 64)         # just before the range
    db.add(HealthMetricChoice(owner_id=me, metric="step_count", enabled=True))
    db.commit()
    data = series.apple_tab_data(db, me, "7d", zone)
    m = data["metrics"][0]
    assert data["today"] == today
    assert m["today"] == 3
    assert m["week"] == 1 + 2 + 4 + 8
    assert dict(m["series"]) == {today: 3.0, today - timedelta(days=1): 4.0, today - timedelta(days=6): 8.0, today - timedelta(days=7): 48.0}
    assert sum(v for _, v in m["series"]) == 127 - 64            # every reading in the range counted exactly once
