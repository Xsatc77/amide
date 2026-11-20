"""Security hardening, shared data and untrusted files: S-03 vendors, S-12 backup zip, backup SQL, N-06 Apple Health,
N-08 parsers, N-09 sharing, S-15 calendar feed. Invented names only."""

import gzip
import hashlib
import io
import json
import struct
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import config
from app.auth import passwords
from app.backup import load as loader
from app.backup import sections as reg
from app.backup.archive import MAX_ENTRIES, read_archive, write_archive
from app.backup.container import BackupError
from app.backup.export import build_archive
from app.db import SessionLocal
from app.ingest import detect, readers, tokens
from app.main import app
from app.models import naive_utcnow as _accepted_now
from app.models import HealthRaw, Share, ShareCategory, User, Vendor
from backup_helpers import clean_files, seed_world, wipe_person


# ---------------------------------------------------------------- a second, ordinary person

def _signed_in(username: str, *, admin: bool = False) -> TestClient:
    """A browser signed in as an ordinary (non-admin) user created straight in the database."""
    with SessionLocal() as s:
        if s.scalar(select(User.id).where(User.username_key == username.lower())) is None:
            s.add(User(username=username, username_key=username.lower(), password_hash=passwords.hash_password("Sec0nd!pw"),
                       is_admin=admin))
            s.commit()
    c = TestClient(app, follow_redirects=False)
    c.post("/notice", data={"understand": "1"})
    r = c.post("/login", data={"username": username, "password": "Sec0nd!pw"})
    assert r.status_code == 303, r.text
    return c


@pytest.fixture(scope="module")
def pat(client):
    return _signed_in("SecPat")


@pytest.fixture(scope="module")
def pat_id(pat):
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == "secpat"))


def _vendor(db, name, created_by):
    v = Vendor(name=name, created_by_id=created_by)
    db.add(v)
    db.commit()
    return v.id


# ---------------------------------------------------------------- S-03 shared vendors

def test_a_non_owner_cannot_edit_or_delete_someone_elses_vendor(client, pat, db, me):
    vid = _vendor(db, "Example Vendor One", me)
    r = pat.post(f"/vendors/{vid}", data={"name": "Hijacked", "website": "https://evil.example"})
    assert r.status_code == 404
    assert pat.post(f"/vendors/{vid}/delete").status_code == 404
    db.expire_all()
    v = db.get(Vendor, vid)
    assert v is not None and v.name == "Example Vendor One" and v.website is None


def test_a_non_owner_cannot_add_a_wallet_to_someone_elses_vendor(client, pat, db, me):
    vid = _vendor(db, "Example Vendor Wallets", me)
    r = pat.post(f"/vendors/{vid}", data={"name": "Example Vendor Wallets", "wallets-0-coin": "BTC",
                                          "wallets-0-address": "bc1qexampleaddress0000"})
    assert r.status_code == 404
    db.expire_all()
    assert db.get(Vendor, vid).wallets == []


def test_everyone_can_still_view_favorite_and_create_vendors(client, pat, db, me):
    vid = _vendor(db, "Example Vendor Two", me)
    assert pat.get("/vendors").status_code == 200
    page = pat.get(f"/vendors/{vid}")
    assert page.status_code == 200 and "Example Vendor Two" in page.text
    assert pat.post(f"/vendors/{vid}/favorite").status_code == 303
    created = pat.post("/vendors", data={"name": "Example Vendor Mine"})
    assert created.status_code == 303 and created.headers["location"].startswith("/vendors/")


def test_the_edit_form_and_delete_are_hidden_from_a_non_owner(client, pat, db, me):
    vid = _vendor(db, "Example Vendor Three", me)
    page = pat.get(f"/vendors/{vid}").text
    assert 'id="vendor-edit-dialog"' not in page and 'data-action="edit-vendor"' not in page
    assert f"/vendors/{vid}/delete" not in pat.get("/vendors").text
    admin_page = client.get(f"/vendors/{vid}").text
    assert 'id="vendor-edit-dialog"' in admin_page and f"/vendors/{vid}/delete" in client.get("/vendors").text


def test_the_creator_can_edit_and_delete_their_own_vendor(client, pat, db, pat_id):
    vid = _vendor(db, "Example Vendor Pat", pat_id)
    assert 'id="vendor-edit-dialog"' in pat.get(f"/vendors/{vid}").text
    r = pat.post(f"/vendors/{vid}", data={"name": "Example Vendor Pat", "website": "https://pat.example"})
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Vendor, vid).website == "https://pat.example"
    assert pat.post(f"/vendors/{vid}/delete").status_code == 303
    db.expire_all()
    assert db.get(Vendor, vid) is None


def test_an_admin_can_edit_a_vendor_someone_else_created_and_one_with_no_creator(client, db, pat_id):
    mine = _vendor(db, "Example Vendor Admin Edit", pat_id)
    orphan = _vendor(db, "Example Vendor No Creator", None)
    assert client.post(f"/vendors/{mine}", data={"name": "Example Vendor Admin Edit", "website": "https://a.example"}, follow_redirects=False).status_code == 303
    assert client.post(f"/vendors/{orphan}", data={"name": "Example Vendor No Creator"}, follow_redirects=False).status_code == 303


def test_a_vendor_with_no_creator_is_editable_by_admins_only(client, pat, db):
    orphan = _vendor(db, "Example Vendor Orphan", None)
    assert pat.post(f"/vendors/{orphan}", data={"name": "Taken"}).status_code == 404
    assert pat.post(f"/vendors/{orphan}/delete").status_code == 404


# ---------------------------------------------------------------- S-12 backup archive

def _zip_with(entries: dict[str, bytes], compression=zipfile.ZIP_STORED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return out.getvalue()


def _forged_archive(entries: dict[str, bytes], compression=zipfile.ZIP_DEFLATED) -> bytes:
    """An archive whose manifest is correct for its entries (so only the size rules can refuse it)."""
    manifest = {"format": 1, "entries": {n: hashlib.sha256(d).hexdigest() for n, d in entries.items()}}
    return _zip_with({"manifest.json": json.dumps(manifest).encode(), **entries}, compression)


def test_a_high_ratio_entry_is_refused_even_under_the_size_limit():
    bomb = _forged_archive({"sections/a.json": b"0" * 20_000_000})
    with pytest.raises(BackupError):
        read_archive(bomb, max_bytes=100_000_000)


def test_too_many_entries_are_refused():
    entries = {f"sections/e{i}.json": b"{}" for i in range(MAX_ENTRIES + 1)}
    with pytest.raises(BackupError):
        read_archive(_forged_archive(entries, zipfile.ZIP_STORED), max_bytes=100_000_000)


def test_an_ordinary_archive_still_reads():
    archive = read_archive(write_archive({"kind": "export"}, {"sections/a.json": b'{"a": 1}'}), max_bytes=1_000_000)
    assert archive.json("sections/a.json") == {"a": 1}


def test_a_central_directory_that_understates_the_size_cannot_bypass_the_limit():
    payload = bytes(range(256)) * 12_000                       # ~3 MB, not very compressible
    blob = bytearray(write_archive({"kind": "export"}, {"sections/a.json": payload}))
    name = b"sections/a.json"
    at = blob.index(b"PK\x01\x02")
    while blob[at + 46:at + 46 + len(name)] != name:           # the central-directory record of our entry
        at = blob.index(b"PK\x01\x02", at + 1)
    struct.pack_into("<I", blob, at + 24, 10)                  # claimed uncompressed size: 10 bytes
    with pytest.raises(BackupError):
        read_archive(bytes(blob), max_bytes=1_000_000)


# ---------------------------------------------------------------- backup SQL: hostile keys are ignored

def _forge(archive, edit):
    entries = {n: archive.read(n) for n in archive.names()}
    for name in list(entries):
        if name.startswith("sections/"):
            payload = json.loads(entries[name])
            edit(payload)
            entries[name] = json.dumps(payload).encode()
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")}
    return read_archive(write_archive(manifest, entries), max_bytes=50_000_000)


@pytest.fixture
def world(client, db, me):
    wipe_person(db, me)
    w = seed_world(db, me)
    yield w
    wipe_person(db, me)
    clean_files()


def test_hostile_keys_in_archive_rows_are_ignored_and_owner_ids_are_overwritten(client, db, me, world):
    archive = build_archive(db, kind="export", uid=me, creator="Tester", keys=["journal", "measurements"])
    archive = read_archive(archive, max_bytes=50_000_000)

    def hostile(payload):
        for rows in payload["tables"].values():
            for row in rows:
                row['x"; DROP TABLE users; --'] = 1
                row["where"] = "1=1"
                row["owner_id"] = 987654

    forged = _forge(archive, hostile)
    wipe_person(db, me)
    loader.load(db, forged, uid=me, username_key="tester", is_admin=True,
                plan={k: loader.ADD for k in ("journal", "measurements")})
    assert db.execute(text("SELECT COUNT(*) FROM users")).scalar() >= 1             # nothing was dropped
    assert db.execute(text("SELECT COUNT(*) FROM journal_entries WHERE owner_id = 987654")).scalar() == 0
    assert db.execute(text("SELECT COUNT(*) FROM journal_entries WHERE owner_id = :u"), {"u": me}).scalar() == 1


def test_a_forged_is_admin_or_password_hash_never_reaches_the_users_table(client, db, me, world):
    # The only user columns the profile loader may write:
    assert set(reg.PROFILE_COLUMNS) == {
        "sex", "birth_date", "height_in", "activity_level", "macro_goal", "diet_preset", "life_stage",
        "custom_protein_pct", "custom_carb_pct", "custom_fat_pct", "water_goal_oz", "timezone", "colorway",
        "prior_gastric_surgery", "measured_tdee", "default_discard_days", "bac_bottle_days", "lyophilized_shelf_days",
        "review_loss_pct", "review_protein_g_per_kg", "review_sleep_low", "review_sleep_share_pct", "review_bp_drop",
        "review_retest_days", "low_stock_default", "shipment_delay_days", "units"}
    for forbidden in ("is_admin", "password_hash", "username", "username_key", "totp_secret", "calendar_token"):
        assert forbidden not in reg.PROFILE_COLUMNS
    db.execute(text("UPDATE users SET height_in = 70 WHERE id = :u"), {"u": me})
    db.commit()
    before = db.execute(text("SELECT username, is_admin, password_hash FROM users WHERE id = :u"), {"u": me}).one()
    archive = read_archive(build_archive(db, kind="export", uid=me, creator="Tester", keys=["profile"]), max_bytes=50_000_000)

    def forge_profile(payload):
        row = payload["tables"]["users"][0]
        row.update({"is_admin": 0, "password_hash": "forged", "username": "pwned", "id": 99999, "totp_secret": "x"})
        row["height_in"] = 65

    run = _forge(archive, forge_profile)
    loader.load(db, run, uid=me, username_key="tester", is_admin=True, plan={"profile": loader.REPLACE})
    db.expire_all()
    after = db.execute(text("SELECT username, is_admin, password_hash, height_in FROM users WHERE id = :u"), {"u": me}).one()
    assert (after.username, after.is_admin, after.password_hash) == tuple(before)
    assert after.height_in == 65                                  # an allowed column did load
    db.execute(text("UPDATE users SET height_in = NULL WHERE id = :u"), {"u": me})
    db.commit()


# ---------------------------------------------------------------- N-06 Apple Health

def _raw_row(db, me, body: bytes, name="x.json.gz") -> HealthRaw:
    config.HEALTH_RAW_DIR.mkdir(parents=True, exist_ok=True)
    (config.HEALTH_RAW_DIR / name).write_bytes(gzip.compress(body))
    row = HealthRaw(owner_id=me, sha256=hashlib.sha256(body[:64] + name.encode()).hexdigest(), size_bytes=len(body),
                    file_name=name, status="stored")
    db.add(row)
    db.commit()
    return row


def test_a_stored_copy_that_expands_beyond_the_cap_is_flagged_not_read(client, db, me, monkeypatch):
    from app.health import parse
    monkeypatch.setattr(config, "HEALTH_MAX_BYTES", 100)               # the expansion cap is 4 times this
    body = b'{"data": {"metrics": []}}' + b" " * 5000
    row = _raw_row(db, me, body)
    parse.import_raw(db, row)
    db.refresh(row)
    assert row.status == "flagged" and "Could not read" in (row.note or "")


def test_absurdly_deep_json_is_flagged_not_a_server_error(client, db, me):
    from app.health import parse
    row = _raw_row(db, me, b"[" * 200_000 + b"]" * 200_000, "deep.json.gz")
    parse.import_raw(db, row)
    db.refresh(row)
    assert row.status == "flagged" and not parse.import_failed(row)


def test_event_ids_stay_sha1_so_stored_events_are_not_duplicated():
    from app.health import parse
    item = {"name": "Run", "start": "2026-01-01 10:00:00 +0000"}
    expected = hashlib.sha1(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()
    assert parse._event_id(item) == expected


def test_a_second_import_for_the_same_owner_at_once_gets_429(client, db, me):
    from app.routers import health_api
    _, secret = tokens.create_token(db, me, "phone", scope="health")
    health_api.limiter._hits.clear()
    health_api._busy_owners.add(me)
    try:
        r = client.post("/api/ingest/health", content=b"{}", headers={"Authorization": f"Bearer {secret}"})
    finally:
        health_api._busy_owners.discard(me)
    assert r.status_code == 429 and r.headers["retry-after"]
    health_api.limiter._hits.clear()
    ok = client.post("/api/ingest/health", content=b'{"data": {"metrics": []}}', headers={"Authorization": f"Bearer {secret}"})
    assert ok.status_code == 200 and me not in health_api._busy_owners


# ---------------------------------------------------------------- N-08 untrusted file parsers

_XLSX_PARTS = {
    "[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
    "xl/workbook.xml": b'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
}


def test_an_xlsx_with_an_entity_declaration_is_refused():
    parts = {**_XLSX_PARTS, "xl/sharedStrings.xml":
             b'<?xml version="1.0"?><!DOCTYPE sst [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><sst>&b;</sst>'}
    with pytest.raises(readers.ReadError):
        readers.read_xlsx(_zip_with(parts, zipfile.ZIP_DEFLATED))


def test_an_xlsx_with_thousands_of_tiny_parts_is_refused():
    parts = {**_XLSX_PARTS, **{f"xl/media/p{i}.xml": b"<a/>" for i in range(3000)}}
    with pytest.raises(readers.ReadError):
        readers.read_xlsx(_zip_with(parts))


def test_an_xlsx_part_that_inflates_too_far_is_refused():
    parts = {**_XLSX_PARTS, "xl/worksheets/sheet1.xml": b"<a>" + b"x" * 30_000_000 + b"</a>"}
    with pytest.raises(readers.ReadError):
        readers.read_xlsx(_zip_with(parts, zipfile.ZIP_DEFLATED))


def test_a_crowded_zip_is_not_detected_as_a_spreadsheet():
    parts = {**_XLSX_PARTS, **{f"xl/media/p{i}.xml": b"<a/>" for i in range(3000)}}
    assert detect.detect_kind(_zip_with(parts)) is None
    assert detect.detect_kind(_zip_with(_XLSX_PARTS)) == "xlsx"


def test_a_crowded_pacer_zip_is_refused_cleanly():
    from app.measurements import pacer
    with pytest.raises(pacer.ImportError_):
        pacer.texts_from_zip(_zip_with({f"steps{i}.csv": b"a" for i in range(3000)}))


def test_a_file_shaped_like_a_pdf_is_a_clean_read_error(tmp_path):
    p = tmp_path / "fake.pdf"
    p.write_bytes(b"%PDF-1.7\n" + b"1 0 obj << /Type /Pages /Kids [1 0 R] /Count 9999999 >> endobj\n" + b"\x00" * 200)
    with pytest.raises(readers.ReadError):
        readers.read_pdf_file(p)


def _pdf(path, pages):
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        w.write(f)


def test_a_pdf_with_too_many_pages_is_refused_by_the_price_list_reader(tmp_path, monkeypatch):
    from app.library.price_lists import reader
    monkeypatch.setattr(reader, "MAX_PDF_PAGES", 2)
    p = tmp_path / "long.pdf"
    _pdf(p, 3)
    with pytest.raises(readers.ReadError, match="too many pages"):
        readers.read_pdf_file(p)


def test_a_workout_pdf_with_too_many_pages_is_not_read(tmp_path, monkeypatch):
    from app.workouts import pdf_parser
    monkeypatch.setattr(pdf_parser, "MAX_PAGES", 2)
    p = tmp_path / "w.pdf"
    _pdf(p, 3)
    with pytest.raises(pdf_parser.TooManyPages):
        pdf_parser.extract_text(p.read_bytes())
    assert pdf_parser.extract_links(p.read_bytes()) == {}


def test_ocr_refuses_an_oversized_image_before_reading_it(monkeypatch):
    from PIL import Image
    from app.library.price_lists import ocr
    monkeypatch.setattr(config, "PHOTO_MAX_PIXELS", 50)
    with pytest.raises(ValueError):
        ocr.recognize(Image.new("RGB", (100, 100)))
    with pytest.raises(readers.ReadError):
        readers.read_images([Image.new("RGB", (100, 100))])


def test_pillow_is_told_the_same_pixel_limit():
    from PIL import Image
    assert Image.MAX_IMAGE_PIXELS == config.PHOTO_MAX_PIXELS


# ---------------------------------------------------------------- N-09 sharing

def test_sharing_cannot_be_used_to_enumerate_users(client, pat_id):
    unknown = client.post("/settings/sharing/99999/inventory", data={"on": "1"})
    with SessionLocal() as s:
        me_id = s.scalar(select(User.id).where(User.username_key == "tester"))
    self_share = client.post(f"/settings/sharing/{me_id}/inventory", data={"on": "1"})
    assert unknown.status_code == self_share.status_code == 404 and unknown.text == self_share.text


def test_the_sharing_table_shows_usernames_only(client, pat_id):
    with SessionLocal() as s:
        s.get(User, pat_id).email = "pat-private@example.test"
        s.commit()
    page = client.get("/settings").text
    start = page.index('id="sharing"')
    section = page[start:page.index('id="medicines"')]
    assert "SecPat" in section and "pat-private@example.test" not in section and "ast login" not in section


def test_shared_with_me_lists_who_shares_and_lets_me_remove_it(client, pat, db, me, pat_id):
    assert client.post(f"/settings/sharing/{pat_id}/inventory", data={"on": "1"}, follow_redirects=False).status_code == 303
    assert "Tester offers to share their inventory with you." in pat.get("/settings").text      # pending: nothing is shared yet
    assert pat.post(f"/settings/shared-with-me/{me}/inventory/accept").status_code == 303
    page = pat.get("/settings").text
    assert "Shared With Me" in page and "Tester shares their inventory with you." in page
    assert pat.post(f"/settings/shared-with-me/{me}/inventory/remove").status_code == 303
    db.expire_all()
    assert db.scalar(select(Share).where(Share.owner_id == me, Share.grantee_id == pat_id)) is None
    assert "Nobody shares their data with you right now." in pat.get("/settings").text


def test_a_person_can_only_remove_shares_granted_to_them(client, pat, db, me, pat_id):
    db.add(Share(owner_id=pat_id, grantee_id=me, category=ShareCategory.INVENTORY, accepted_at=_accepted_now()))
    db.commit()
    try:
        # Pat granted this to the admin; Pat cannot use the "received" route to remove it, and an unknown pair is a plain 404
        assert pat.post(f"/settings/shared-with-me/{me}/inventory/remove").status_code == 404
        assert pat.post("/settings/shared-with-me/99999/inventory/remove").status_code == 404
        assert pat.post(f"/settings/shared-with-me/{me}/nonsense/remove").status_code == 404
        assert db.scalar(select(Share).where(Share.owner_id == pat_id, Share.grantee_id == me)) is not None
        assert client.post(f"/settings/shared-with-me/{pat_id}/inventory/remove", follow_redirects=False).status_code == 303
        db.expire_all()
        assert db.scalar(select(Share).where(Share.owner_id == pat_id, Share.grantee_id == me)) is None
    finally:
        db.query(Share).delete()
        db.commit()


# ---------------------------------------------------------------- S-15 calendar feed

@pytest.fixture
def feed_address(client, db, me):
    from app.routers import calendar
    calendar.feed_limiter._hits.clear()
    client.post("/settings/calendar-feed", data={"action": "create"})
    db.expire_all()
    token = db.get(User, me).calendar_token
    yield f"/calendar/feed/{token}.ics"
    calendar.feed_limiter._hits.clear()


def test_the_calendar_feed_is_private_and_sends_no_referrer(client, feed_address):
    r = client.get(feed_address)
    assert r.status_code == 200
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "no-store" in r.headers["cache-control"] and "private" in r.headers["cache-control"]


def test_the_calendar_feed_is_rate_limited_per_address(client, feed_address):
    codes = [client.get(feed_address).status_code for _ in range(62)]
    assert codes[:60] == [200] * 60 and codes[60:] == [429, 429]
    assert client.get("/calendar/feed/not-a-token.ics").status_code == 429       # guesses count against the same limit
