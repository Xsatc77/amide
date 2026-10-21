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
