import pytest

from app import config
from app.backup import load as loader
from app.backup.archive import read_archive
from app.backup.container import BackupError
from app.backup.export import build_archive
from app.models import BodyPhoto, User
from photo_helpers import make_photo, other_client


def export(db, me, keys, kind="backup"):
    return read_archive(build_archive(db, kind=kind, uid=me, creator="Tester", keys=keys), max_bytes=50_000_000)


def run(db, me, archive, plan):
    return loader.load(db, archive, uid=me, username_key="tester", is_admin=True, plan=plan)


def test_a_personal_backup_carries_photo_rows_and_files(client, db, me):
    photo = make_photo(db, me, angle="front", note="Start")
    archive = export(db, me, ["body_photos"])
    assert f"files/body_photos/{photo.filename}" in archive.names()
    assert any("body_photos" in name for name in archive.names() if not name.startswith("files/"))


def test_a_share_file_can_never_contain_photos(client, db, me):
    make_photo(db, me)
    with pytest.raises(BackupError, match="cannot be put in a share file"):
        export(db, me, ["body_photos"], kind="share")
    archive = export(db, me, ["inventory"], kind="share")
    assert not [n for n in archive.names() if "body_photos" in n]


def test_loading_photos_adds_them_with_their_files_and_replace_swaps_them(client, db, me):
    make_photo(db, me, note="one")
    archive = export(db, me, ["body_photos"])
    run(db, me, archive, {"body_photos": loader.ADD})
    db.expire_all()
    assert db.query(BodyPhoto).filter_by(owner_id=me).count() == 2          # photos have no natural key: Add appends
    run(db, me, archive, {"body_photos": loader.REPLACE})
    db.expire_all()
    rows = db.query(BodyPhoto).filter_by(owner_id=me).all()
    assert len(rows) == 1 and all((config.BODY_PHOTO_DIR / r.filename).exists() for r in rows)


def test_photos_load_for_the_loader_never_for_the_name_in_the_file(client, db, me):
    make_photo(db, me)
    archive = export(db, me, ["body_photos"])
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        loader.load(db, archive, uid=other_id, username_key="photoother", is_admin=False, plan={"body_photos": loader.ADD})
        db.expire_all()
        assert db.query(BodyPhoto).filter_by(owner_id=other_id).count() == 1


def test_deleting_an_account_removes_its_photo_files(client, db, me):
    with other_client("photogone"):
        gone = db.query(User).filter_by(username_key="photogone").one()
        gone_id, gone_name = gone.id, gone.username
        photo = make_photo(db, gone_id)
        path = config.BODY_PHOTO_DIR / photo.filename
        r = client.post(f"/settings/admin/users/{gone_id}/delete", data={"username": gone_name}, follow_redirects=False)
        assert r.status_code == 303
    db.expire_all()
    assert db.query(BodyPhoto).filter_by(owner_id=gone_id).count() == 0 and not path.exists()


def test_the_backup_page_offers_body_photos_for_personal_backups(client, db, me):
    assert "Body photos" in client.get("/backup").text
