from datetime import datetime

import pytest

from app import config
from app.ingest import tokens
from app.models import IngestItem, IngestSource, IngestToken, User, Vendor
from ingest_helpers import anon_client, bearer, jpeg_bytes, make_source, make_token, pdf_bytes, png_bytes, xlsx_bytes

NOW = "2026-10-06T14:30:00+00:00"


def post_message(c, secret, source, files=(), **fields):
    data = {"chat_id": source.chat_id, "message_id": "10", "date": NOW, **fields}
    return c.post("/api/ingest/messages", data=data, headers=bearer(secret),
                  files=[("files", (name, content, "application/octet-stream")) for name, content in files] or None)


# ---------------------------------------------------------------- tokens

def test_a_token_is_shown_once_and_only_its_hash_is_stored(db, me):
    token, secret = tokens.create_token(db, me, "Home PC")
    assert secret.startswith(tokens.PREFIX) and len(secret) > 40
    assert secret not in (token.token_hash, token.prefix) and token.token_hash == tokens.hash_secret(secret)
    assert tokens.find_token(db, f"Bearer {secret}").id == token.id


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer nope", "Basic abc", "Bearer " + "x" * 300])
def test_malformed_headers_find_no_token(db, header):
    assert tokens.find_token(db, header) is None


def test_a_revoked_token_or_one_whose_owner_is_no_longer_an_administrator_stops_working(db, me):
    token, secret = tokens.create_token(db, me, "t")
    assert tokens.revoke_token(db, token.id) is True
    assert tokens.find_token(db, f"Bearer {secret}") is None
    token2, secret2 = tokens.create_token(db, me, "t2")
    db.get(User, me).is_admin = False
    db.commit()
    try:
        assert tokens.find_token(db, f"Bearer {secret2}") is None
    finally:
        db.get(User, me).is_admin = True
        db.commit()


def test_the_rate_limit_allows_a_fixed_number_a_minute_per_token_then_recovers():
    limiter = tokens.RateLimiter(3, 60.0)
    assert [limiter.allow(1, t) for t in (0, 1, 2, 3)] == [True, True, True, False]
    assert limiter.allow(2, 3) is True
    assert limiter.allow(1, 61) is True


# ---------------------------------------------------------------- authentication on the routes

def test_every_ingest_route_refuses_a_missing_or_wrong_token_with_a_fixed_message(db, me):
    source = make_source(db)
    with anon_client() as c:
        for r in (c.get("/api/ingest/sources"), c.put(f"/api/ingest/sources/{source.chat_id}", json={"title": "x"}),
                  c.post(f"/api/ingest/sources/{source.chat_id}/state", json={"state": "gone"}),
                  c.post("/api/ingest/messages", data={"chat_id": source.chat_id, "message_id": "1", "date": NOW}),
                  c.get("/api/ingest/sources", headers=bearer("amide_ing_wrong"))):
            assert r.status_code == 401 and r.json() == {"detail": "Unauthorized"}


def test_a_signed_in_browser_without_a_token_is_still_refused(client, db, me):
    make_source(db)
    assert client.get("/api/ingest/sources").status_code == 401


def test_requests_beyond_the_rate_limit_get_429(db, me, monkeypatch):
    secret = make_token(db, me)
    monkeypatch.setattr(tokens, "limiter", tokens.RateLimiter(2, 60.0))
    with anon_client() as c:
        codes = [c.get("/api/ingest/sources", headers=bearer(secret)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ---------------------------------------------------------------- sources

def test_the_watcher_registers_a_group_which_starts_disabled_and_unmapped(db, me):
    secret = make_token(db, me)
    with anon_client() as c:
        r = c.put("/api/ingest/sources/-100777", json={"title": "Acme Peptides group"}, headers=bearer(secret))
        assert r.status_code == 200 and r.json() == {"chat_id": "-100777", "title": "Acme Peptides group", "enabled": False, "mapped": False}
        assert c.put("/api/ingest/sources/-100777", json={"title": "Renamed"}, headers=bearer(secret)).json()["title"] == "Renamed"
        assert c.get("/api/ingest/sources", headers=bearer(secret)).json() == []
    assert db.query(IngestSource).count() == 1


def test_only_enabled_and_mapped_groups_are_listed_for_watching(db, me):
    secret = make_token(db, me)
    vendor = Vendor(name="Acme Labs")
    db.add(vendor)
    db.commit()
    make_source(db, vendor=vendor, chat_id="-1", title="Mapped and enabled")
    make_source(db, vendor=vendor, chat_id="-2", title="Disabled", enabled=False)
    make_source(db, vendor=None, chat_id="-3", title="Unmapped")
    with anon_client() as c:
        assert c.get("/api/ingest/sources", headers=bearer(secret)).json() == [{"chat_id": "-1", "title": "Mapped and enabled"}]


def test_the_watcher_reports_a_group_gone_and_active_again(db, me):
    secret = make_token(db, me)
    source = make_source(db)
    with anon_client() as c:
        r = c.post(f"/api/ingest/sources/{source.chat_id}/state", json={"state": "gone", "reason": "removed from the group"}, headers=bearer(secret))
        assert r.status_code == 200
        db.expire_all()
        s = db.get(IngestSource, source.id)
        assert (s.state, s.state_reason) == ("gone", "removed from the group") and s.state_changed_at is not None
        first = s.state_changed_at
        c.post(f"/api/ingest/sources/{source.chat_id}/state", json={"state": "gone"}, headers=bearer(secret))
        db.expire_all()
        assert db.get(IngestSource, source.id).state_changed_at == first
        c.post(f"/api/ingest/sources/{source.chat_id}/state", json={"state": "active"}, headers=bearer(secret))
        db.expire_all()
        assert db.get(IngestSource, source.id).state == "active"
        assert c.post(f"/api/ingest/sources/{source.chat_id}/state", json={"state": "lost"}, headers=bearer(secret)).status_code == 422
        assert c.post("/api/ingest/sources/-404/state", json={"state": "gone"}, headers=bearer(secret)).status_code == 404


# ---------------------------------------------------------------- messages

def test_a_pdf_is_stored_as_a_received_item_with_its_context(db, me):
    secret = make_token(db, me)
    source = make_source(db)
    with anon_client() as c:
        r = post_message(c, secret, source, files=[("list.pdf", pdf_bytes())], text="New prices, USA warehouse")
    assert r.status_code == 200 and r.json()["results"][0]["status"] == "received"
    db.expire_all()
    item = db.query(IngestItem).one()
    assert (item.kind, item.status, item.filename, item.caption, item.message_id) == ("pdf", "received", "list.pdf", "New prices, USA warehouse", "10")
    assert item.received_at == datetime(2026, 10, 6, 14, 30) and (config.INGEST_DIR / item.stored_file).read_bytes()[:4] == b"%PDF"


def test_the_kind_comes_from_the_bytes_not_the_name(db, me):
    secret = make_token(db, me)
    source = make_source(db)
    with anon_client() as c:
        r = post_message(c, secret, source, files=[("renamed.txt", pdf_bytes()), ("sheet.pdf", xlsx_bytes([["a", "b"]])), ("photo.bin", png_bytes()),
                                                  ("pic.jpg", jpeg_bytes()), ("note.pdf", b"just words, not a pdf"), ("empty.pdf", b"")])
    assert [x["status"] for x in r.json()["results"]] == ["received", "received", "received", "received", "ignored", "ignored"]
    assert sorted(i.kind for i in db.query(IngestItem)) == ["image", "image", "pdf", "xlsx"]


def test_typed_text_alone_is_an_item_and_empty_text_is_not(db, me):
    secret = make_token(db, me)
    source = make_source(db)
    with anon_client() as c:
        assert post_message(c, secret, source, text="Zorvex ZX10 10mg*10vials $50").json()["results"][0]["status"] == "received"
        assert post_message(c, secret, source, text="   ", message_id="11").json()["results"] == []
    assert db.query(IngestItem).filter_by(kind="text").count() == 1


def test_the_same_message_and_file_is_a_duplicate_and_changes_nothing(db, me):
    secret = make_token(db, me)
    source = make_source(db)
    with anon_client() as c:
        post_message(c, secret, source, files=[("a.pdf", pdf_bytes())])
        again = post_message(c, secret, source, files=[("a.pdf", pdf_bytes())])
        other_message = post_message(c, secret, source, files=[("a.pdf", pdf_bytes())], message_id="11")
    assert again.json()["results"][0]["status"] == "duplicate"
    assert other_message.json()["results"][0]["status"] == "received"
    assert db.query(IngestItem).count() == 2


def test_limits_on_size_file_count_text_and_unknown_or_disabled_sources(db, me, monkeypatch):
    secret = make_token(db, me)
    source = make_source(db)
    disabled = make_source(db, chat_id="-9", enabled=False)
    monkeypatch.setattr(config, "INGEST_MAX_FILE_BYTES", 100)
    with anon_client() as c:
        big = post_message(c, secret, source, files=[("big.pdf", pdf_bytes() + b"0" * 500)])
        assert big.json()["results"][0] == {"status": "rejected", "reason": "file too large", "item_id": None}
        monkeypatch.setattr(config, "INGEST_MAX_FILE_BYTES", 25 * 1024 * 1024)
        many = post_message(c, secret, source, files=[(f"{i}.pdf", pdf_bytes() + bytes([i])) for i in range(11)], message_id="12")
        assert many.status_code == 422
        assert post_message(c, secret, source, text="x" * 8001, message_id="13").status_code == 422
        unknown = c.post("/api/ingest/messages", data={"chat_id": "-404", "message_id": "1", "date": NOW}, headers=bearer(secret))
        assert unknown.status_code == 404
        assert post_message(c, secret, disabled, files=[("a.pdf", pdf_bytes())]).status_code == 409
        assert post_message(c, secret, source, message_id="14", date="not-a-date").status_code == 422
    assert db.query(IngestItem).count() == 0


def test_photos_of_one_album_share_a_group_key_and_lone_photos_within_five_minutes_cluster(db, me):
    secret = make_token(db, me)
    source = make_source(db)
    with anon_client() as c:
        post_message(c, secret, source, files=[("1.png", png_bytes(1))], message_id="1", album_id="A")
        post_message(c, secret, source, files=[("2.png", png_bytes(2))], message_id="2", album_id="A")
        post_message(c, secret, source, files=[("3.png", png_bytes(3))], message_id="3")
        post_message(c, secret, source, files=[("4.png", png_bytes(4))], message_id="4")
        post_message(c, secret, source, files=[("5.pdf", pdf_bytes())], message_id="5")
    keys = {i.message_id: i.group_key for i in db.query(IngestItem)}
    assert keys["1"] == keys["2"] and keys["3"] == keys["4"] and keys["1"] != keys["3"] and keys["5"] not in (keys["1"], keys["3"])


def test_the_last_use_of_a_token_is_recorded(db, me):
    secret = make_token(db, me)
    with anon_client() as c:
        c.get("/api/ingest/sources", headers=bearer(secret))
    db.expire_all()
    assert db.query(IngestToken).one().last_used_at is not None
