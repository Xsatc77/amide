import gzip
from datetime import datetime, timedelta

from app import config
from app.health import history
from app.models import HealthRaw, HealthReading, User, naive_utcnow


def reading(db, owner, metric, start, value=1.0, source="Phone"):
    db.add(HealthReading(owner_id=owner, metric=metric, start_at=start, end_at=start + timedelta(minutes=5), grain="minute",
                         value=value, unit="count", source=source, extra={"max": value}))
    db.commit()


def test_history_filters_by_range_and_metrics_and_owner(db, me):
    other = User(username="someone-else", username_key="someone-else", password_hash="x")
    db.add(other)
    db.commit()
    reading(db, me, "heart_rate", datetime(2024, 2, 6, 9), 70)
    reading(db, me, "step_count", datetime(2024, 2, 6, 8), 100)
    reading(db, me, "heart_rate", datetime(2024, 2, 9, 9), 80)           # outside the range
    reading(db, other.id, "heart_rate", datetime(2024, 2, 6, 10), 99)    # someone else's
    got = history.history(db, me, datetime(2024, 2, 6), datetime(2024, 2, 7))
    assert [(r["metric"], r["value"]) for r in got] == [("step_count", 100.0), ("heart_rate", 70.0)]       # ordered by start
    assert got[1] == {"metric": "heart_rate", "start": "2024-02-06T09:00:00+00:00", "end": "2024-02-06T09:05:00+00:00",
                      "value": 70.0, "unit": "count", "source": "Phone", "extra": {"max": 70.0}}
    only = history.history(db, me, datetime(2024, 2, 1), datetime(2024, 2, 28), ["heart_rate"])
    assert [r["value"] for r in only] == [70.0, 80.0]
    db.query(HealthReading).delete()
    db.delete(other)                                  # the shared test database must not keep a second user (the settings page lists them)
    db.commit()


def raw_row(db, me, name, age_days, today):
    (config.HEALTH_RAW_DIR).mkdir(parents=True, exist_ok=True)
    if name != "gone":
        (config.HEALTH_RAW_DIR / f"{name}.json.gz").write_bytes(gzip.compress(b"{}"))
    row = HealthRaw(owner_id=me, sha256=name, size_bytes=2, file_name=f"{name}.json.gz", received_at=today - timedelta(days=age_days))
    db.add(row)
    db.commit()


def test_purge_removes_old_raw_files_and_rows_but_keeps_readings(db, me):
    today = naive_utcnow()
    raw_row(db, me, "old-one", 100, today)
    raw_row(db, me, "fresh-one", 10, today)
    reading(db, me, "heart_rate", datetime(2024, 2, 6, 9))
    assert history.purge_raw(db, today) == 1
    assert [r.sha256 for r in db.query(HealthRaw)] == ["fresh-one"]
    assert not (config.HEALTH_RAW_DIR / "old-one.json.gz").exists()
    assert (config.HEALTH_RAW_DIR / "fresh-one.json.gz").exists()
    assert db.query(HealthReading).count() == 1


def test_purge_survives_a_missing_file(db, me):
    today = naive_utcnow()
    raw_row(db, me, "gone", 200, today)
    assert history.purge_raw(db, today) == 1
    assert db.query(HealthRaw).count() == 0


def test_purge_removes_stray_files_older_than_an_hour_but_never_fresh_ones(db, me):
    import os
    import time
    today = naive_utcnow()
    raw_row(db, me, "kept-one", 1, today)
    directory = config.HEALTH_RAW_DIR
    stray, fresh, incoming = directory / "stray.json.gz", directory / "fresh-stray.json.gz", directory / "incoming-abc.tmp"
    for f in (stray, fresh, incoming):
        f.write_bytes(b"x")
    old = time.time() - 2 * 3600
    os.utime(stray, (old, old))
    os.utime(incoming, (old, old))                       # a temp file left by a crash an hour ago is stray too
    os.utime(directory / "kept-one.json.gz", (old, old))   # old, but its row still owns it
    history.purge_raw(db, today)
    assert not stray.exists() and not incoming.exists()
    assert fresh.exists() and (directory / "kept-one.json.gz").exists()
    fresh.unlink()


def test_purge_skips_rows_that_kept_no_file(db, me):
    today = naive_utcnow()
    db.add(HealthRaw(owner_id=me, sha256="nofile", size_bytes=2, file_name="", received_at=today - timedelta(days=200)))
    db.commit()
    assert history.purge_raw(db, today) == 1 and config.HEALTH_RAW_DIR.is_dir()


def test_a_failing_daily_run_is_logged_not_swallowed(monkeypatch, caplog):
    def broken(*args):
        raise RuntimeError("purge broke")
    monkeypatch.setattr(history, "run_purge", broken)
    with caplog.at_level("ERROR", logger="app.health.history"):
        history.daily_once(lambda: None)
    assert "purge broke" in caplog.text or any(r.exc_info for r in caplog.records)


def test_the_daily_run_also_reprocesses_failed_exports(monkeypatch):
    from app.health import parse
    seen = []
    monkeypatch.setattr(history, "run_purge", lambda factory, now: seen.append("purge") or 0)
    monkeypatch.setattr(parse, "run_reprocess", lambda factory: seen.append("reprocess") or 0)
    history.daily_once(lambda: None)
    assert seen == ["purge", "reprocess"]
