import json

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import config
from app.backup import restore
from app.backup.archive import read_archive, write_archive
from app.backup.container import BackupError, unseal
from app.backup.export import build_archive
from app.db import Base, SessionLocal, make_engine
from app.models import User
from backup_helpers import seed_world

PASS = "safety passphrase"
FILE_DIRS = ("COA_DIR", "PRICE_LIST_DIR", "LAB_REPORT_DIR", "WORKOUT_PDF_DIR", "WALLET_QR_DIR")


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """A private database and private file directories, so restoring never touches the shared test database."""
    for name in FILE_DIRS:
        monkeypatch.setattr(config, name, tmp_path / "uploads" / name.lower())
    monkeypatch.setattr(config, "CARDS_DIR", tmp_path / "library" / "cards")
    monkeypatch.setattr(config, "CARDS_JSON", tmp_path / "library" / "cards.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    engine = make_engine(f"sqlite:///{(tmp_path / 'scratch.db').as_posix()}")
    Base.metadata.create_all(engine)
    with SessionLocal() as shared:
        head = shared.execute(text("SELECT version_num FROM alembic_version")).scalar()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.exec_driver_sql("INSERT INTO alembic_version VALUES (?)", (head,))
    with Session(engine) as s:
        admin = User(username="Admin", username_key="admin", password_hash="hash-admin", is_admin=True)
        other = User(username="Other", username_key="other", password_hash="hash-other", totp_secret="SECRET")
        s.add_all([admin, other])
        s.commit()
        seed_world(s, admin.id, "A")
        seed_world(s, other.id, "B")
        s.execute(text("INSERT INTO sessions (id, user_id, twofa_pending, created_at, last_seen) "
                       "VALUES ('tok', :u, 0, '2026-01-01', '2026-01-01')"), {"u": admin.id})
        s.commit()
        yield s, admin.id, tmp_path
    engine.dispose()


def snapshot(s):
    """Every row of every table (sorted), and the attached files, as plain data to compare."""
    tables = {}
    for (name,) in s.execute(text("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT IN "
                                  "('sessions', 'alembic_version', 'sqlite_sequence') ORDER BY name")):
        rows = [dict(r) for r in s.execute(text(f'SELECT * FROM "{name}"')).mappings()]
        tables[name] = sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)
    files = {}
    for key in FILE_DIRS:
        directory = getattr(config, key)
        files[key] = {p.name: p.read_bytes() for p in directory.glob("*")} if directory.is_dir() else {}
    return tables, files


def whole(s, uid):
    return read_archive(build_archive(s, kind="backup", uid=uid, creator="Admin", keys=[], installation=True),
                        max_bytes=50_000_000)


def test_restoring_everything_rebuilds_every_table_with_its_ids_and_its_files(scratch):
    s, admin, _ = scratch
    archive = whole(s, admin)
    expected = snapshot(s)
    s.execute(text("DELETE FROM inventory_items WHERE owner_id = :u"), {"u": admin})
    s.execute(text("DELETE FROM journal_entries"))
    s.execute(text("UPDATE users SET password_hash = 'tampered'"))
    s.execute(text("INSERT INTO vendors (name, created_at) VALUES ('Junk Vendor', '2026-01-01')"))
    s.commit()
    (config.COA_DIR / "junk.pdf").write_bytes(b"junk")
    for f in config.WALLET_QR_DIR.glob("*"):
        f.unlink()
    report = restore.restore_installation(s, archive, uid=admin, creator="Admin", safety_passphrase=PASS)
    assert snapshot(s) == expected                                    # same rows, same ids, same files, nothing extra
    assert not (config.COA_DIR / "junk.pdf").exists() and report.files == 10
    assert report.rows["users"] == 2 and report.rows["inventory_items"] == 2


def test_restoring_signs_everyone_out(scratch):
    s, admin, _ = scratch
    assert s.execute(text("SELECT COUNT(*) FROM sessions")).scalar() == 1
    s.commit()
    restore.restore_installation(s, whole(s, admin), uid=admin, creator="Admin", safety_passphrase=PASS)
    assert s.execute(text("SELECT COUNT(*) FROM sessions")).scalar() == 0


def test_a_safety_backup_of_what_was_replaced_is_written_and_readable_and_only_three_are_kept(scratch):
    s, admin, tmp = scratch
    archive = whole(s, admin)
    for _ in range(4):
        restore.restore_installation(s, archive, uid=admin, creator="Admin", safety_passphrase=PASS)
    copies = sorted((tmp / "backups").glob("amide-safety-*.amidebackup"))
    assert len(copies) <= 3 and copies
    inner = read_archive(unseal(copies[-1].read_bytes(), PASS), max_bytes=50_000_000)
    assert inner.manifest["level"] == "installation" and inner.has("sections/accounts.json")
    with pytest.raises(BackupError, match="Wrong passphrase"):
        unseal(copies[-1].read_bytes(), "another passphrase")


def test_an_inconsistent_backup_changes_nothing(scratch):
    s, admin, tmp = scratch
    archive = whole(s, admin)
    entries = {n: archive.read(n) for n in archive.names()}
    key = next(n for n in entries if n.startswith("persons/admin/") and n.endswith("inventory.json"))
    payload = json.loads(entries[key])
    payload["tables"]["order_items"][0]["inventory_item_id"] = 424242        # a parent that is not in the file
    entries[key] = json.dumps(payload).encode()
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")}
    broken = read_archive(write_archive(manifest, entries), max_bytes=50_000_000)
    before = snapshot(s)
    with pytest.raises(BackupError, match="inconsistent"):
        restore.restore_installation(s, broken, uid=admin, creator="Admin", safety_passphrase=PASS)
    assert snapshot(s) == before
    assert not list((tmp / "backups").glob(".restore-*"))                      # staging removed


def test_only_a_whole_installation_backup_can_restore_everything(scratch):
    s, admin, _ = scratch
    person = read_archive(build_archive(s, kind="backup", uid=admin, creator="Admin", keys=["journal"]), max_bytes=50_000_000)
    with pytest.raises(BackupError, match="whole-installation"):
        restore.restore_installation(s, person, uid=admin, creator="Admin", safety_passphrase=PASS)


def test_a_newer_backup_and_a_short_safety_passphrase_are_refused_before_anything_happens(scratch):
    s, admin, tmp = scratch
    archive = whole(s, admin)
    entries = {n: archive.read(n) for n in archive.names()}
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")} | {"revision": "9999"}
    newer = read_archive(write_archive(manifest, entries), max_bytes=50_000_000)
    before = snapshot(s)
    with pytest.raises(BackupError, match="newer version"):
        restore.restore_installation(s, newer, uid=admin, creator="Admin", safety_passphrase=PASS)
    with pytest.raises(BackupError, match="at least 8"):
        restore.restore_installation(s, archive, uid=admin, creator="Admin", safety_passphrase="short")
    assert snapshot(s) == before and not (tmp / "backups").exists()


def test_an_older_backup_restores_with_defaults_for_columns_it_did_not_have(scratch):
    s, admin, _ = scratch
    archive = whole(s, admin)
    entries = {n: archive.read(n) for n in archive.names()}
    key = next(n for n in entries if n.startswith("persons/admin/") and n.endswith("workouts.json"))
    payload = json.loads(entries[key])
    for row in payload["tables"]["workout_exercise_logs"]:
        row.pop("net_kcal")
    entries[key] = json.dumps(payload).encode()
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")} | {"revision": "0001"}
    restore.restore_installation(s, read_archive(write_archive(manifest, entries), max_bytes=50_000_000),
                                 uid=admin, creator="Admin", safety_passphrase=PASS)
    assert s.execute(text("SELECT COUNT(*) FROM workout_exercise_logs WHERE net_kcal IS NULL")).scalar() >= 1


def test_if_the_safety_backup_cannot_be_written_nothing_is_changed(scratch, monkeypatch):
    s, admin, tmp = scratch
    archive = whole(s, admin)
    before = snapshot(s)

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(restore, "write_safety_backup", broken)
    with pytest.raises(BackupError, match="safety backup could not be written"):
        restore.restore_installation(s, archive, uid=admin, creator="Admin", safety_passphrase=PASS)
    assert snapshot(s) == before
