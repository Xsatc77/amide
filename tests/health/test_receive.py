import gzip
import json

import pytest

from app import config
from app.ingest import tokens
from app.models import HealthRaw
from app.routers import health_api

BODY = json.dumps({"data": {"metrics": [{"name": "step_count", "units": "count", "data": []}]}}).encode()


@pytest.fixture(autouse=True)
def fresh_limiter():
    health_api.limiter._hits.clear()
    yield


@pytest.fixture
def auth(db, me):
    _, secret = tokens.create_token(db, me, "phone", scope="health")
    return {"Authorization": f"Bearer {secret}"}


def _files():
    return set(config.HEALTH_RAW_DIR.iterdir()) if config.HEALTH_RAW_DIR.exists() else set()


def test_no_token_is_401(client):
    assert client.post("/api/ingest/health", content=BODY).status_code == 401


def test_valid_post_stores_a_gzipped_copy_and_answers_200(client, db, auth):
    r = client.post("/api/ingest/health", content=BODY, headers={**auth, "Automation-Name": "Nightly", "Session-Id": "s1"})
    assert r.status_code == 200 and r.json() == {"received": len(BODY), "readings": 0, "duplicate": False}
    row = db.query(HealthRaw).one()
    assert row.status == "parsed" and row.size_bytes == len(BODY) and row.automation_name == "Nightly"
    assert row.headers == {"automation-name": "Nightly", "session-id": "s1"}
    assert gzip.decompress((config.HEALTH_RAW_DIR / row.file_name).read_bytes()) == BODY
    assert not list(config.HEALTH_RAW_DIR.glob("*.tmp"))


def test_same_body_twice_is_a_duplicate_and_adds_no_row(client, db, auth):
    client.post("/api/ingest/health", content=BODY, headers=auth)
    files = _files()
    r = client.post("/api/ingest/health", content=BODY, headers=auth)
    assert r.status_code == 200 and r.json()["duplicate"] is True
    assert db.query(HealthRaw).count() == 1
    assert _files() == files          # no second copy, no leftover temp file


def test_body_over_the_cap_is_413_and_stores_nothing(client, db, auth, monkeypatch):
    monkeypatch.setattr(config, "HEALTH_MAX_BYTES", 1000)
    before = _files()
    r = client.post("/api/ingest/health", content=b"{" + b" " * 2000 + b"}", headers=auth)
    assert r.status_code == 413
    assert db.query(HealthRaw).count() == 0 and _files() == before


def test_chunked_body_without_length_is_capped_too(client, db, auth, monkeypatch):
    monkeypatch.setattr(config, "HEALTH_MAX_BYTES", 1000)
    before = _files()

    def chunks():
        for _ in range(10):
            yield b" " * 300

    r = client.post("/api/ingest/health", content=chunks(), headers=auth)
    assert "content-length" not in r.request.headers and r.status_code == 413
    assert db.query(HealthRaw).count() == 0 and _files() == before


def test_a_body_that_is_not_json_is_stored_and_flagged_not_rejected(client, db, auth):
    r = client.post("/api/ingest/health", content=b"this is not json", headers=auth)
    assert r.status_code == 200
    row = db.query(HealthRaw).one()
    assert row.status == "flagged" and row.note


def test_rate_limit_is_429(client, auth, monkeypatch):
    monkeypatch.setattr(health_api, "limiter", tokens.RateLimiter(2, 60.0))
    codes = [client.post("/api/ingest/health", content=BODY, headers=auth).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_the_default_cap_is_64_mb_and_a_bad_setting_falls_back(monkeypatch):
    mb = 1024 * 1024
    monkeypatch.delenv("AMIDE_HEALTH_MAX_MB", raising=False)
    assert config._env_mb("AMIDE_HEALTH_MAX_MB", 64) == 64 * mb
    monkeypatch.setenv("AMIDE_HEALTH_MAX_MB", "lots")
    assert config._env_mb("AMIDE_HEALTH_MAX_MB", 64) == 64 * mb       # a typo must not stop Amide starting
    monkeypatch.setenv("AMIDE_HEALTH_MAX_MB", "-5")
    assert config._env_mb("AMIDE_HEALTH_MAX_MB", 64) == 64 * mb
    monkeypatch.setenv("AMIDE_HEALTH_MAX_MB", "200")
    assert config._env_mb("AMIDE_HEALTH_MAX_MB", 64) == 200 * mb
    assert config.HEALTH_RAW_QUOTA_BYTES == 2048 * mb


def test_a_body_that_is_not_json_keeps_no_file(client, db, auth):
    before = _files()
    r = client.post("/api/ingest/health", content=b"definitely not json " * 50, headers=auth)
    assert r.status_code == 200
    row = db.query(HealthRaw).one()
    assert row.status == "flagged" and row.file_name == "" and row.size_bytes == 1000 and "not kept" in row.note
    assert _files() == before
    r = client.post("/api/ingest/health", content=b"definitely not json " * 50, headers=auth)     # the same again: still nothing kept, no error
    assert r.status_code == 200 and r.json()["duplicate"] is True and _files() == before


def _steps_body(n):
    return json.dumps({"data": {"metrics": [{"name": "step_count", "units": "count",
                                             "data": [{"qty": n, "date": "2024-02-06 10:00:00 +0000", "source": "Phone"}]}]}}).encode()


def test_over_the_quota_the_oldest_parsed_copies_go_first(client, db, auth, monkeypatch):
    first, second, third = _steps_body(1), _steps_body(2), _steps_body(3)
    monkeypatch.setattr(config, "HEALTH_RAW_QUOTA_BYTES", len(first) + len(second) + 10)
    before = {f.name for f in _files()}
    for body in (first, second):
        assert client.post("/api/ingest/health", content=body, headers=auth).status_code == 200
    assert client.post("/api/ingest/health", content=third, headers=auth).status_code == 200
    rows = db.query(HealthRaw).order_by(HealthRaw.id).all()
    assert [r.file_name == "" for r in rows] == [True, False, False]
    assert rows[0].note == "raw copy removed to stay within the storage limit" and rows[0].status == "parsed"
    assert {f.name for f in _files()} - before == {rows[1].file_name, rows[2].file_name}


def test_when_nothing_can_be_freed_the_answer_is_507_and_nothing_is_kept(client, db, auth, monkeypatch):
    body = _steps_body(1)
    monkeypatch.setattr(config, "HEALTH_RAW_QUOTA_BYTES", len(body) - 1)
    before = _files()
    r = client.post("/api/ingest/health", content=body, headers=auth)
    assert r.status_code == 507
    assert db.query(HealthRaw).count() == 0 and _files() == before


def test_unparsed_copies_are_never_removed_for_the_quota(client, db, auth, monkeypatch):
    from app.health import parse
    first, second = _steps_body(1), _steps_body(2)
    monkeypatch.setattr(config, "HEALTH_RAW_QUOTA_BYTES", len(first) + len(second) - 1)
    monkeypatch.setattr(parse, "import_raw", lambda session, raw: 0)      # it stays "stored", so it may still be needed
    client.post("/api/ingest/health", content=first, headers=auth)
    assert client.post("/api/ingest/health", content=second, headers=auth).status_code == 507
    row = db.query(HealthRaw).one()
    assert row.file_name and (config.HEALTH_RAW_DIR / row.file_name).exists()


def test_the_quota_is_per_person(client, db, auth, monkeypatch):
    from app.models import User
    other = User(username="quota-pal", username_key="quota-pal", password_hash="x")
    db.add(other)
    db.commit()
    db.add(HealthRaw(owner_id=other.id, sha256="z" * 64, size_bytes=10 ** 9, file_name="someone-else.json.gz", status="parsed"))
    db.commit()
    monkeypatch.setattr(config, "HEALTH_RAW_QUOTA_BYTES", 10_000)
    try:
        assert client.post("/api/ingest/health", content=_steps_body(1), headers=auth).status_code == 200
        assert db.query(HealthRaw).filter_by(owner_id=other.id).one().file_name == "someone-else.json.gz"
    finally:
        db.query(HealthRaw).delete()
        db.delete(other)
        db.commit()


def test_an_import_that_fails_unexpectedly_is_503_so_the_phone_resends(client, db, auth, monkeypatch):
    from app.health import parse

    def broken(*args, **kwargs):
        raise RuntimeError("database went away")
    monkeypatch.setattr(parse, "_import_readings", broken)
    body = _steps_body(5)
    r = client.post("/api/ingest/health", content=body, headers=auth)
    assert r.status_code == 503
    row = db.query(HealthRaw).one()
    assert row.status == "flagged" and row.note.startswith("Import failed")
    monkeypatch.undo()
    health_api.limiter._hits.clear()
    r = client.post("/api/ingest/health", content=body, headers=auth)          # the phone's resend now goes through
    assert r.status_code == 200 and r.json()["readings"] == 1


def test_bad_content_that_was_skipped_stays_200(client, db, auth):
    body = json.dumps({"data": {"metrics": [{"name": "step_count", "units": "count", "data": [{"qty": "null", "date": "2024-02-06 10:00:00 +0000"}]}]}}).encode()
    r = client.post("/api/ingest/health", content=body, headers=auth)
    assert r.status_code == 200 and db.query(HealthRaw).one().status == "flagged"


def test_a_resend_restores_a_stored_copy_that_went_missing(client, db, auth, monkeypatch):
    from app.health import parse
    body = _steps_body(7)
    monkeypatch.setattr(parse, "import_raw", lambda session, raw: 0)
    client.post("/api/ingest/health", content=body, headers=auth)
    row = db.query(HealthRaw).one()
    (config.HEALTH_RAW_DIR / row.file_name).unlink()
    monkeypatch.undo()
    health_api.limiter._hits.clear()
    r = client.post("/api/ingest/health", content=body, headers=auth)
    assert r.status_code == 200 and r.json()["readings"] == 1
    db.expire_all()
    assert gzip.decompress((config.HEALTH_RAW_DIR / db.query(HealthRaw).one().file_name).read_bytes()) == body


def test_an_import_that_always_fails_gives_up_with_200_after_five_tries(client, db, auth, monkeypatch):
    from app.health import parse

    def broken(*args, **kwargs):
        raise RuntimeError("always")
    monkeypatch.setattr(parse, "_import_readings", broken)
    body = _steps_body(9)
    codes = []
    for _ in range(5):
        health_api.limiter._hits.clear()
        codes.append(client.post("/api/ingest/health", content=body, headers=auth).status_code)
    assert codes == [503, 503, 503, 503, 200]                  # the fifth answer lets the phone move on
    db.expire_all()
    row = db.query(HealthRaw).one()
    assert row.status == "flagged" and row.attempts == 5 and "gave up after 5 tries; fix needed" in row.note.lower()
    assert not parse.import_failed(row) and parse.reprocess_flagged(db) == 0       # the daily pass stops retrying it
    health_api.limiter._hits.clear()
    assert client.post("/api/ingest/health", content=body, headers=auth).status_code == 503     # a manual resend gets a fresh 5 tries
    db.expire_all()
    row = db.query(HealthRaw).one()
    assert row.attempts == 1 and row.note.startswith(parse.FAILED)
    monkeypatch.undo()                                         # the fix is in: a resend reads it
    health_api.limiter._hits.clear()
    r = client.post("/api/ingest/health", content=body, headers=auth)
    assert r.status_code == 200 and r.json()["readings"] == 1
    db.expire_all()
    assert db.query(HealthRaw).one().status == "parsed"


def test_over_the_quota_failed_and_stranded_copies_go_after_parsed_ones(client, db, auth, me, monkeypatch):
    from datetime import timedelta
    from app.health import receive
    from app.models import naive_utcnow
    body = _steps_body(4)
    config.HEALTH_RAW_DIR.mkdir(parents=True, exist_ok=True)

    def kept(name, status, note, hours_ago):
        (config.HEALTH_RAW_DIR / name).write_bytes(gzip.compress(b"{}"))
        row = HealthRaw(owner_id=me, sha256=name.ljust(64, "0"), size_bytes=len(body), file_name=name, status=status, note=note,
                        received_at=naive_utcnow() - timedelta(hours=hours_ago))
        db.add(row)
        db.commit()
        return row
    failed = kept("failed.json.gz", "flagged", "Import failed: OperationalError", 5)
    stranded = kept("stranded.json.gz", "stored", None, 3)              # a crash left it unread for hours
    young = kept("young.json.gz", "stored", None, 0)                    # may be being read right now: never removed
    monkeypatch.setattr(config, "HEALTH_RAW_QUOTA_BYTES", len(body) * 2)
    assert client.post("/api/ingest/health", content=body, headers=auth).status_code == 200
    db.expire_all()
    for row, name in ((failed, "failed.json.gz"), (stranded, "stranded.json.gz")):
        row = db.get(HealthRaw, row.id)
        assert row.file_name == "" and row.note.startswith(receive.QUOTA_NOTE) and row.status == "flagged"      # the row stays, with a note
        assert not (config.HEALTH_RAW_DIR / name).exists()
    assert db.get(HealthRaw, young.id).file_name == "young.json.gz" and (config.HEALTH_RAW_DIR / "young.json.gz").exists()
    newest = db.query(HealthRaw).order_by(HealthRaw.id.desc()).first()
    assert newest.file_name and (config.HEALTH_RAW_DIR / newest.file_name).exists()


def test_the_copy_of_the_body_being_stored_is_never_removed_for_room(db, me, monkeypatch):
    from datetime import timedelta
    from app.health import receive
    from app.models import naive_utcnow
    config.HEALTH_RAW_DIR.mkdir(parents=True, exist_ok=True)
    (config.HEALTH_RAW_DIR / "same.json.gz").write_bytes(b"x")
    db.add(HealthRaw(owner_id=me, sha256="s" * 64, size_bytes=100, file_name="same.json.gz", status="flagged", note="Import failed: x",
                     received_at=naive_utcnow() - timedelta(hours=5)))
    db.commit()
    monkeypatch.setattr(config, "HEALTH_RAW_QUOTA_BYTES", 150)
    assert receive._make_room(db, me, 100, "s" * 64) is False
    assert (config.HEALTH_RAW_DIR / "same.json.gz").exists()


def test_a_resend_of_an_evicted_unread_copy_stores_it_again_and_reads_it(client, db, auth, monkeypatch):
    from app.health import parse, receive

    def broken(*args, **kwargs):
        raise RuntimeError("locked")
    monkeypatch.setattr(parse, "_import_readings", broken)
    body = _steps_body(6)
    assert client.post("/api/ingest/health", content=body, headers=auth).status_code == 503
    row = db.query(HealthRaw).one()
    (config.HEALTH_RAW_DIR / row.file_name).unlink()                  # removed for the quota while it waited for the resend
    row.file_name, row.note = "", f"{receive.QUOTA_NOTE} before it was read in full ({row.note})"
    db.commit()
    monkeypatch.undo()
    health_api.limiter._hits.clear()
    r = client.post("/api/ingest/health", content=body, headers=auth)   # the phone's resend of the same bytes
    assert r.status_code == 200 and r.json() == {"received": len(body), "readings": 1, "duplicate": True}
    db.expire_all()
    row = db.query(HealthRaw).one()
    assert row.status == "parsed" and row.file_name and gzip.decompress((config.HEALTH_RAW_DIR / row.file_name).read_bytes()) == body


def test_a_resend_of_an_evicted_copy_that_does_not_fit_is_507(client, db, auth, monkeypatch):
    from app.health import parse
    monkeypatch.setattr(parse, "_import_readings", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("locked")))
    body = _steps_body(8)
    client.post("/api/ingest/health", content=body, headers=auth)
    row = db.query(HealthRaw).one()
    (config.HEALTH_RAW_DIR / row.file_name).unlink()
    row.file_name = ""
    db.commit()
    monkeypatch.setattr(config, "HEALTH_RAW_QUOTA_BYTES", len(body) - 1)
    health_api.limiter._hits.clear()
    before = _files()
    assert client.post("/api/ingest/health", content=body, headers=auth).status_code == 507
    assert _files() == before and db.query(HealthRaw).count() == 1


def test_a_not_json_duplicate_still_keeps_no_file(client, db, auth):
    client.post("/api/ingest/health", content=b"nope " * 10, headers=auth)
    before = _files()
    r = client.post("/api/ingest/health", content=b"nope " * 10, headers=auth)
    assert r.status_code == 200 and _files() == before
