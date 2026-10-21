import gzip
import json
from datetime import datetime

import pytest

from app import config
from app.health import parse
from app.health.groups import RECOMMENDED, SECTIONS, metric_group
from app.ingest import tokens
from app.models import HealthEvent, HealthRaw, HealthReading
from app.routers import health_api


@pytest.fixture(autouse=True)
def fresh_limiter():
    health_api.limiter._hits.clear()
    yield


def make_raw(db, me, payload, headers=None, *, as_bytes=None):
    """Writes an invented export where the receiver would and returns its row."""
    body = as_bytes if as_bytes is not None else json.dumps(payload).encode()
    config.HEALTH_RAW_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"t-{db.query(HealthRaw).count()}.json.gz"
    (config.HEALTH_RAW_DIR / file_name).write_bytes(gzip.compress(body))
    raw = HealthRaw(owner_id=me, sha256=file_name, size_bytes=len(body), file_name=file_name, headers=headers)
    db.add(raw)
    db.commit()
    return raw


def steps(*points):
    return {"metrics": [{"name": "step_count", "units": "count", "data": [{"qty": q, "date": d, "source": "Phone"} for d, q in points]}]}


def test_parse_date_with_offset_and_bare_day():
    assert parse.parse_date("2024-02-06 14:30:00 -0800") == (datetime(2024, 2, 6, 22, 30), -480)
    assert parse.parse_date("2024-02-06 14:30:00 +0530") == (datetime(2024, 2, 6, 9, 0), 330)
    assert parse.parse_date("2024-02-06") == (datetime(2024, 2, 6), 0)
    with pytest.raises(ValueError):
        parse.parse_date("yesterday")


def test_steps_become_readings_with_the_header_grain(db, me):
    raw = make_raw(db, me, steps(("2024-02-06 10:00:00 -0800", 120), ("2024-02-06 11:00:00 -0800", 80)),
                   {"automation-aggregation": "Hours"})
    assert parse.import_raw(db, raw) == 2
    rows = db.query(HealthReading).order_by(HealthReading.start_at).all()
    assert [(r.metric, r.value, r.unit, r.grain, r.source, r.offset_minutes) for r in rows] == [
        ("step_count", 120, "count", "hour", "Phone", -480), ("step_count", 80, "count", "hour", "Phone", -480)]
    assert rows[0].start_at == datetime(2024, 2, 6, 18) and rows[0].end_at == datetime(2024, 2, 6, 19)
    assert (raw.status, raw.readings_count, raw.note) == ("parsed", 2, None)
    assert parse.GRAIN_SECONDS == {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def test_grain_is_point_without_or_with_an_unknown_header(db, me):
    for i, headers in enumerate([None, {"automation-aggregation": "weeks"}, {"automation-aggregation": "fortnights"}]):
        parse.import_raw(db, make_raw(db, me, steps((f"2024-02-0{i + 1} 10:00:00 +0000", 5)), headers))
    rows = db.query(HealthReading).all()
    assert len(rows) == 3 and {r.grain for r in rows} == {"point"} and all(r.end_at == r.start_at for r in rows)


def test_reposting_updates_not_duplicates(db, me):
    first = make_raw(db, me, steps(("2024-02-06 10:00:00 -0800", 120)), {"automation-aggregation": "hours"})
    parse.import_raw(db, first)
    second = make_raw(db, me, steps(("2024-02-06 10:00:00 -0800", 150)), {"automation-aggregation": "hours"})
    assert parse.import_raw(db, second) == 1
    assert [r.value for r in db.query(HealthReading).all()] == [150]


def test_blood_pressure_heart_rate_and_sleep_shapes(db, me):
    payload = {"metrics": [
        {"name": "blood_pressure", "units": "mmHg", "data": [{"systolic": 118, "diastolic": 76, "date": "2024-02-06 08:00:00 +0000", "source": "Cuff"}]},
        {"name": "heart_rate", "units": "count/min", "data": [{"Min": 52, "Avg": 64.5, "Max": 101, "date": "2024-02-06 09:00:00 +0000", "source": "Watch"}]},
        {"name": "sleep_analysis", "units": "hr", "data": [{"date": "2024-02-06 00:00:00 +0000", "totalSleep": 7.25, "asleep": 0, "core": 4.1, "deep": 1.2,
                                                           "rem": 1.95, "inBed": 8.0, "sleepStart": "2024-02-05 23:10:00 +0000",
                                                           "sleepEnd": "2024-02-06 06:40:00 +0000", "source": "Watch"}]},
    ]}
    assert parse.import_raw(db, make_raw(db, me, payload, {"automation-aggregation": "days"})) == 3
    bp, hr, sl = (db.query(HealthReading).filter_by(metric=m).one() for m in ("blood_pressure", "heart_rate", "sleep_analysis"))
    assert bp.value == 118 and bp.extra == {"systolic": 118, "diastolic": 76}
    assert hr.value == 64.5 and hr.extra == {"min": 52, "max": 101}
    assert sl.value == 7.25 and sl.unit == "hr"
    # Not established: whether aggregated sleep carries exactly these stage names
    assert sl.extra["core"] == 4.1 and sl.extra["deep"] == 1.2 and sl.extra["rem"] == 1.95 and sl.extra["inBed"] == 8.0


def test_null_string_and_missing_date_readings_are_skipped_and_flagged_while_the_rest_import(db, me):
    payload = {"metrics": [{"name": "step_count", "units": "count", "data": [
        {"qty": 10, "date": "2024-02-06 10:00:00 +0000", "source": "Phone"},
        {"qty": "null", "date": "2024-02-06 11:00:00 +0000"},
        {"qty": None, "date": "2024-02-06 12:00:00 +0000"},
        {"qty": 7},
        {"qty": 9, "date": "not a date"},
        "junk",
        {"qty": 11, "date": "2024-02-06 13:00:00 +0000", "source": "Phone"},
    ]}]}
    raw = make_raw(db, me, payload)
    assert parse.import_raw(db, raw) == 2
    assert db.query(HealthReading).count() == 2
    assert raw.status == "flagged" and raw.note.startswith("5 item(s) skipped") and raw.readings_count == 2


def test_unknown_metric_is_kept_and_grouped_under_mind_and_other(db, me):
    payload = {"metrics": [{"name": "zephyr_index", "units": "zp", "data": [{"qty": 3.5, "date": "2024-02-06 10:00:00 +0000"}]}]}
    assert parse.import_raw(db, make_raw(db, me, payload)) == 1
    assert db.query(HealthReading).one().metric == "zephyr_index"
    assert metric_group("zephyr_index") == "Mind and other" == SECTIONS[-1]
    assert metric_group("step_count") == "Activity" and metric_group("weight_&_body_mass") == "Body"
    assert metric_group("dietary_water") == "Nutrition and water" and metric_group("sleep_analysis") == "Sleep"
    assert metric_group("heart_rate") == "Heart and vitals"
    assert {"step_count", "fiber", "event:symptom", "event:state_of_mind"} <= RECOMMENDED
    assert "heart_rate" not in RECOMMENDED and "sleep_analysis" not in RECOMMENDED


def test_data_wrapped_and_unwrapped_payloads_both_work(db, me):
    # Not established: whether Health Auto Export nests the arrays under a "data" key
    assert parse.import_raw(db, make_raw(db, me, steps(("2024-02-06 10:00:00 +0000", 1)))) == 1
    assert parse.import_raw(db, make_raw(db, me, {"data": steps(("2024-02-07 10:00:00 +0000", 2))})) == 1
    assert db.query(HealthReading).count() == 2


def test_workout_symptom_state_of_mind_and_unknown_event_kinds_are_stored_with_full_payload(db, me):
    route = [{"latitude": 1.5, "longitude": 2.5, "timestamp": "2024-02-06 07:00:05 +0000"}]
    payload = {"workouts": [{"id": "W-1", "name": "Walking", "start": "2024-02-06 07:00:00 +0000", "end": "2024-02-06 07:30:00 +0000",
                             "duration": 1800, "distance": {"qty": 2.1, "units": "km"}, "route": route}],
               # Not established: the shapes of symptoms, state of mind, ECG, heart rate notifications, cycle tracking and medications
               "symptoms": [{"id": "S-1", "name": "Headache", "start": "2024-02-06 09:00:00 +0000", "severity": "mild"}],
               "state_of_mind": [{"id": "M-1", "kind": "daily_mood", "start": "2024-02-06 20:00:00 +0000", "valence": 0.4, "labels": ["calm"]}],
               "ecg": [{"start": "2024-02-06 10:00:00 +0000", "classification": "sinus", "voltageMeasurements": [0.1, 0.2, 0.3]}],
               "heart_rate_notifications": [{"start": "2024-02-06 11:00:00 +0000", "threshold": 120}],
               "cycle_tracking": [{"name": "Period", "start": "2024-02-01 00:00:00 +0000"}],
               "medications": [{"name": "Zephyrol", "start": "2024-02-06 08:00:00 +0000", "dose": "1"}]}
    raw = make_raw(db, me, payload)
    assert parse.import_raw(db, raw) == 0 and raw.status == "parsed"
    events = {e.kind: e for e in db.query(HealthEvent).all()}
    assert set(events) == {"workout", "symptom", "state_of_mind", "ecg", "heart_rate_notification", "cycle", "medication"}
    w = events["workout"]
    assert w.external_id == "W-1" and w.name == "Walking" and w.start_at == datetime(2024, 2, 6, 7) and w.end_at == datetime(2024, 2, 6, 7, 30)
    assert w.payload == payload["workouts"][0] and w.payload["route"] == route
    assert events["symptom"].external_id == "S-1" and events["state_of_mind"].payload["labels"] == ["calm"]
    assert events["ecg"].payload["voltageMeasurements"] == [0.1, 0.2, 0.3]
    assert len(events["ecg"].external_id) == 40            # no id: the SHA-1 of the item
    parse.import_raw(db, make_raw(db, me, payload))        # a repost does not duplicate events
    assert db.query(HealthEvent).count() == 7


def test_an_unknown_top_level_kind_flags_the_export_while_known_kinds_import(db, me):
    payload = {**steps(("2024-02-06 10:00:00 +0000", 3)), "symptoms": [{"id": "S-9", "name": "Cough", "start": "2024-02-06 09:00:00 +0000"}],
               "zephyr_events": [{"id": "Z-1"}]}
    raw = make_raw(db, me, payload)
    assert parse.import_raw(db, raw) == 1
    assert raw.status == "flagged" and "unknown: zephyr_events" in raw.note
    assert db.query(HealthEvent).one().external_id == "S-9"


def test_a_long_event_id_is_hashed_and_a_long_unit_is_cut(db, me):
    payload = {"metrics": [{"name": "step_count", "units": "u" * 50, "data": [{"qty": 1, "date": "2024-02-06 10:00:00 +0000"}]}],
               "symptoms": [{"id": "x" * 200, "start": "2024-02-06 09:00:00 +0000"}]}
    assert parse.import_raw(db, make_raw(db, me, payload)) == 1
    assert len(db.query(HealthReading).one().unit) == 30
    assert len(db.query(HealthEvent).one().external_id) == 40


def test_a_post_loads_and_touches_only_rows_in_its_own_time_span(db, me):
    hourly = {"automation-aggregation": "hours"}
    old = steps(("2023-01-01 10:00:00 +0000", 11), ("2023-01-01 11:00:00 +0000", 12))
    parse.import_raw(db, make_raw(db, me, old, hourly))
    parse.import_raw(db, make_raw(db, me, steps(("2024-02-06 10:00:00 +0000", 5)), hourly))
    loaded = []
    from sqlalchemy import event as sa_event
    db.expunge_all()
    hook = lambda target, ctx: loaded.append(target.value)
    sa_event.listen(HealthReading, "load", hook)
    parse.import_raw(db, make_raw(db, me, steps(("2024-02-06 10:00:00 +0000", 6), ("2024-02-06 12:00:00 +0000", 7)), hourly))
    sa_event.remove(HealthReading, "load", hook)
    assert loaded == [5]                                              # only the overlapping row was read; the 2023 rows never were
    assert sorted(r.value for r in db.query(HealthReading).all()) == [6, 7, 11, 12]     # overlap upserted, nothing duplicated


def test_garbage_json_is_flagged_not_raised(db, me):
    for body in (b"not json at all", b'{"metrics": [', b"[1, 2", b"\xff\xfe\x00", b"", b"42", b'{"nothing": true}'):
        raw = make_raw(db, me, None, as_bytes=body)
        assert parse.import_raw(db, raw) == 0
        assert raw.status == "flagged" and raw.note, body
    missing = HealthRaw(owner_id=me, sha256="gone", size_bytes=1, file_name="does-not-exist.json.gz")
    db.add(missing)
    db.commit()
    assert parse.import_raw(db, missing) == 0 and missing.status == "flagged"
    assert db.query(HealthReading).count() == 0


def test_bom_prefixed_json_parses(db, me):
    raw = make_raw(db, me, None, as_bytes=b"\xef\xbb\xbf" + json.dumps(steps(("2024-02-06 10:00:00 +0000", 4))).encode())
    assert parse.import_raw(db, raw) == 1 and raw.status == "parsed"


def test_an_unexpected_error_rolls_the_whole_export_back(db, me, monkeypatch):
    raw = make_raw(db, me, steps(("2024-02-06 10:00:00 +0000", 1), ("2024-02-06 11:00:00 +0000", 2)))
    real = parse._qty
    calls = []

    def boom(value):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(value)

    monkeypatch.setattr(parse, "_qty", boom)
    assert parse.import_raw(db, raw) == 0
    assert db.query(HealthReading).count() == 0 and raw.status == "flagged" and raw.readings_count == 0


@pytest.fixture
def auth(db, me):
    _, secret = tokens.create_token(db, me, "phone", scope="health")
    return {"Authorization": f"Bearer {secret}"}


def test_receiver_tolerates_a_bom_when_judging_json(client, db, auth):
    body = b"\xef\xbb\xbf" + json.dumps(steps(("2024-02-06 10:00:00 +0000", 4))).encode()
    r = client.post("/api/ingest/health", content=body, headers=auth)
    assert r.json()["readings"] == 1
    assert db.query(HealthRaw).one().status == "parsed"      # not flagged as "not JSON"


def test_a_duplicate_of_an_unparsed_export_is_reparsed(client, db, auth, monkeypatch):
    body = json.dumps(steps(("2024-02-06 10:00:00 +0000", 4))).encode()
    real = parse.import_raw
    monkeypatch.setattr(parse, "import_raw", lambda session, raw: 0)    # a broken parser leaves it stored
    client.post("/api/ingest/health", content=body, headers=auth)
    assert db.query(HealthRaw).one().status == "stored"
    monkeypatch.setattr(parse, "import_raw", real)
    r = client.post("/api/ingest/health", content=body, headers=auth)
    assert r.json() == {"received": len(body), "readings": 1, "duplicate": True}
    assert db.query(HealthRaw).count() == 1 and db.query(HealthReading).count() == 1
    calls = []
    monkeypatch.setattr(parse, "import_raw", lambda session, raw: calls.append(1) or 0)
    r = client.post("/api/ingest/health", content=body, headers=auth)   # parsed now: not parsed again
    assert r.json()["readings"] == 1 and not calls


def test_each_metric_and_event_kind_commits_on_its_own(db, me, monkeypatch):
    from sqlalchemy import event
    payload = {"metrics": [{"name": n, "units": "count", "data": [{"qty": 1, "date": f"2024-02-06 1{i}:00:00 +0000"} for i in range(5)]}
                           for n in ("step_count", "flights_climbed", "push_count")],
               "workouts": [{"id": "w1", "name": "Walk", "start": "2024-02-06 10:00:00 +0000"}],
               "symptoms": [{"name": "Headache", "start": "2024-02-06 10:00:00 +0000"}]}
    raw = make_raw(db, me, payload)
    monkeypatch.setattr(parse, "CHUNK", 2)                    # five rows a metric: three chunks each
    commits = []

    def count(session):
        commits.append(1)
    event.listen(db, "after_commit", count)
    try:
        assert parse.import_raw(db, raw) == 15
    finally:
        event.remove(db, "after_commit", count)
    assert len(commits) >= 3 * 3 + 2 + 1          # nine reading chunks, two event kinds, the final status
    assert raw.status == "parsed"


def test_reprocess_reruns_exports_that_failed_but_not_ones_with_skipped_items(db, me, monkeypatch):
    good = make_raw(db, me, steps(("2024-02-06 10:00:00 +0000", 4)))
    skipped = make_raw(db, me, {"metrics": [{"name": "step_count", "data": [{"qty": "null", "date": "2024-02-06 11:00:00 +0000"}]}]})
    real = parse._import_readings

    def broken(*args, **kwargs):
        raise RuntimeError("locked")
    monkeypatch.setattr(parse, "_import_readings", broken)
    parse.import_raw(db, good)
    assert good.status == "flagged" and good.note.startswith("Import failed")
    monkeypatch.setattr(parse, "_import_readings", real)
    parse.import_raw(db, skipped)
    assert skipped.status == "flagged" and "skipped" in skipped.note
    gone = HealthRaw(owner_id=me, sha256="gone-again", size_bytes=1, file_name="", status="flagged", note="Import failed: OSError")
    db.add(gone)
    db.commit()
    calls = []
    monkeypatch.setattr(parse, "import_raw", lambda s, r, real=parse.import_raw: calls.append(r.id) or real(s, r))
    assert parse.reprocess_flagged(db) == 1
    assert calls == [good.id]
    db.expire_all()
    assert db.get(HealthRaw, good.id).status == "parsed" and db.query(HealthReading).count() == 1
    assert parse.reprocess_flagged(db) == 0                    # nothing left that failed


def test_a_row_without_a_kept_file_is_left_alone(db, me):
    raw = HealthRaw(owner_id=me, sha256="nofile", size_bytes=5, file_name="", status="flagged", note="The body does not look like JSON; it was not kept")
    db.add(raw)
    db.commit()
    assert parse.import_raw(db, raw) == 0
    assert raw.status == "flagged" and raw.note.startswith("The body does not look like JSON")


def test_reprocess_passes_over_lost_copies_so_they_cannot_use_up_the_limit(db, me):
    lost = HealthRaw(owner_id=me, sha256="lost", size_bytes=1, file_name="lost-copy.json.gz", status="flagged", note="Import failed: x")
    db.add(lost)
    db.commit()
    good = make_raw(db, me, steps(("2024-02-06 10:00:00 +0000", 4)))
    good.status, good.note = "flagged", "Import failed: OperationalError"
    db.commit()
    assert parse.reprocess_flagged(db, limit=1) == 1
    assert good.status == "parsed" and lost.status == "flagged"


def test_without_the_header_date_only_values_are_days_and_timed_values_points(db, me):
    payload = {"metrics": [{"name": "step_count", "units": "count", "data": [{"qty": 8000, "date": "2024-02-06", "source": "Phone"},
                                                                          {"qty": 12, "date": "2024-02-06 10:00:00 +0000", "source": "Phone"}]}]}
    assert parse.import_raw(db, make_raw(db, me, payload)) == 2
    rows = {r.grain: r for r in db.query(HealthReading).all()}
    assert set(rows) == {"day", "point"}
    assert rows["day"].start_at == datetime(2024, 2, 6) and rows["day"].end_at == datetime(2024, 2, 7)
    assert rows["point"].end_at == rows["point"].start_at
    parse.import_raw(db, make_raw(db, me, {"metrics": [{"name": "step_count", "data": [{"qty": 8100, "date": "2024-02-06", "source": "Phone"}]}]}))
    assert sorted(r.value for r in db.query(HealthReading).all()) == [12, 8100]       # the day re-sent updates its row
    parse.import_raw(db, make_raw(db, me, steps(("2024-02-07", 5)), {"automation-aggregation": "hours"}))
    assert db.query(HealthReading).filter_by(value=5).one().grain == "hour"           # a header still decides when it is there


def test_a_twin_import_that_fails_never_turns_a_finished_row_into_failed(db, me, monkeypatch):
    from app.db import SessionLocal
    raw = make_raw(db, me, steps(("2024-02-06 10:00:00 +0000", 4)))

    def twin_finishes_then_this_one_fails(*args, **kwargs):
        with SessionLocal() as other:                          # the concurrent duplicate post completes the same export first
            row = other.get(HealthRaw, raw.id)
            row.status, row.readings_count, row.note = "parsed", 1, None
            other.commit()
        raise RuntimeError("database is locked")
    monkeypatch.setattr(parse, "_import_readings", twin_finishes_then_this_one_fails)
    assert parse.import_raw(db, raw) == 1
    db.expire_all()
    row = db.get(HealthRaw, raw.id)
    assert (row.status, row.readings_count, row.note) == ("parsed", 1, None)


def test_reprocess_picks_up_exports_a_crash_left_stored_for_over_an_hour(db, me):
    from datetime import timedelta
    from app.models import naive_utcnow
    old = make_raw(db, me, steps(("2024-02-06 10:00:00 +0000", 4)))
    young = make_raw(db, me, steps(("2024-02-07 10:00:00 +0000", 5)))
    stuck = make_raw(db, me, steps(("2024-02-08 10:00:00 +0000", 6)))
    old.received_at = stuck.received_at = naive_utcnow() - timedelta(hours=2)
    young.received_at = naive_utcnow() - timedelta(minutes=10)            # may still be importing right now
    stuck.attempts = 5                                                      # crashed the process five times
    db.commit()
    assert parse.reprocess_flagged(db) == 1
    db.expire_all()
    assert db.get(HealthRaw, old.id).status == "parsed" and db.get(HealthRaw, young.id).status == "stored"
    gave_up = db.get(HealthRaw, stuck.id)
    assert gave_up.status == "flagged" and "gave up after 5 tries" in gave_up.note.lower()
    assert db.query(HealthReading).count() == 1
