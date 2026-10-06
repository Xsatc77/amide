"""Fixes from the independent review of the body photos feature."""

import io
import json

import pytest
from PIL import Image

from app import config
from app.backup import load as loader
from app.backup import restore
from app.backup.archive import read_archive, write_archive
from app.backup.container import BackupError, unseal
from app.backup.export import build_archive
from app.models import BodyPhoto, User
from app.body_photos import process_upload
from photo_helpers import code_now, enable_2fa, gradient, jpeg, make_photo, png
from test_backup_restore import PASS, scratch, whole  # noqa: F401  (scratch is a fixture)

PASSPHRASE = "pass phrase 1"


def turn_on(db, me):
    secret = enable_2fa(db, me)
    db.get(User, me).photo_2fa_required = True
    db.commit()
    return secret


def create(client, **fields):
    data = {"kind": "export", "section": "body_photos", "passphrase": PASSPHRASE, "confirm": PASSPHRASE, **fields}
    return client.post("/backup/create", data=data)


def rewrite(archive, entries):
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")}
    return read_archive(write_archive(manifest, entries), max_bytes=50_000_000)


# ---------------------------------------------------------------- Critical: backups do not bypass the photo lock

@pytest.mark.parametrize("kind", ["export", "backup"])
def test_a_backup_or_export_with_photos_is_refused_while_the_photo_lock_is_on(client, db, me, kind):
    turn_on(db, me)
    photo = make_photo(db, me)
    r = create(client, kind=kind)
    assert r.status_code == 422 and "unlock" in r.text.lower() and r.headers["content-type"].startswith("text/html")
    assert photo.filename not in r.text


def test_after_unlocking_the_same_export_works_and_holds_the_photo(client, db, me):
    secret = turn_on(db, me)
    photo = make_photo(db, me)
    client.post("/measurements/photos/unlock", data={"code": code_now(secret)})
    r = create(client)
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    names = read_archive(unseal(r.content, PASSPHRASE), max_bytes=50_000_000).names()
    assert f"files/body_photos/{photo.filename}" in names


def test_other_sections_are_not_held_back_by_the_photo_lock(client, db, me):
    turn_on(db, me)
    make_photo(db, me)
    assert create(client, section="journal").status_code == 200


def test_with_the_setting_off_photos_export_without_a_code(client, db, me):
    make_photo(db, me)
    assert create(client).status_code == 200


def test_a_whole_installation_backup_by_an_administrator_with_the_lock_on_needs_the_unlock_too(client, db, me):
    turn_on(db, me)
    make_photo(db, me)
    r = client.post("/backup/create", data={"kind": "backup", "scope": "installation", "passphrase": PASSPHRASE,
                                            "confirm": PASSPHRASE})
    assert r.status_code == 422 and "unlock" in r.text.lower()


# ---------------------------------------------------------------- Important: no metadata, not even a JPEG comment

def test_a_jpeg_comment_is_not_carried_into_the_stored_photo():
    buffer = io.BytesIO()
    gradient().save(buffer, "JPEG", comment=b"12 Home Street")
    cleaned = Image.open(io.BytesIO(process_upload(buffer.getvalue())))
    assert "comment" not in cleaned.info and b"Home Street" not in process_upload(buffer.getvalue())


# ---------------------------------------------------------------- Important: a damaged stored file is a 404, not a crash

def test_a_damaged_stored_file_gives_404_for_the_preview(client, db, me):
    photo = make_photo(db, me)
    (config.BODY_PHOTO_DIR / photo.filename).write_bytes(b"not an image any more")
    assert client.get(f"/measurements/photos/{photo.id}/preview").status_code == 404


# ---------------------------------------------------------------- Important: files loaded from a backup are checked

def exported(db, me, entry_bytes):
    photo = make_photo(db, me, note="from file")
    archive = read_archive(build_archive(db, kind="export", uid=me, creator="Tester", keys=["body_photos"]),
                           max_bytes=50_000_000)
    entries = {n: archive.read(n) for n in archive.names()}
    entries[f"files/body_photos/{photo.filename}"] = entry_bytes
    return rewrite(archive, entries), photo


def test_loading_a_photo_whose_file_is_not_an_image_skips_that_photo(client, db, me):
    archive, photo = exported(db, me, b"this is not an image")
    report = loader.load(db, archive, uid=me, username_key="tester", is_admin=True, plan={"body_photos": loader.REPLACE})
    db.expire_all()
    assert db.query(BodyPhoto).filter_by(owner_id=me).count() == 0
    assert report.unresolved and not [f for f in config.BODY_PHOTO_DIR.glob("*")]


def test_a_loaded_photo_is_re_encoded_clean_and_gets_a_name_this_app_can_serve(client, db, me):
    archive, photo = exported(db, me, png())
    loader.load(db, archive, uid=me, username_key="tester", is_admin=True, plan={"body_photos": loader.REPLACE})
    db.expire_all()
    (row,) = db.query(BodyPhoto).filter_by(owner_id=me).all()
    assert row.filename.endswith(".jpg") and len(row.filename) == 36
    assert Image.open(config.BODY_PHOTO_DIR / row.filename).format == "JPEG"
    assert client.get(f"/measurements/photos/{row.id}/preview").status_code == 200


def test_restore_refuses_a_photo_row_with_a_name_the_app_cannot_serve(scratch):
    s, admin, _ = scratch
    archive = whole(s, admin)
    entries = {n: archive.read(n) for n in archive.names()}
    key = next(n for n in entries if n.startswith("persons/admin/") and n.endswith("body_photos.json"))
    payload = json.loads(entries[key])
    payload["tables"]["body_photos"] = [{"id": 1, "owner_id": admin, "taken_on": "2026-10-01", "angle": None, "note": None,
                                         "filename": "evil.png", "created_at": "2026-10-01 00:00:00"}]
    entries[key] = json.dumps(payload).encode()
    with pytest.raises(BackupError):
        restore.restore_installation(s, rewrite(archive, entries), uid=admin, creator="Admin", safety_passphrase=PASS)
