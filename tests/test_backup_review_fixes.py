import io
import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import text

from app import config
from app.backup import container, load as loader, pending, restore
from app.backup.archive import _safe, read_archive, write_archive
from app.backup.container import BackupError, seal, unseal
from app.backup.export import build_archive
from app.models import InventoryItem
from backup_helpers import clean_files, person_counts, seed_world, wipe_person
from test_backup_restore import PASS, scratch, snapshot, whole  # noqa: F401  (scratch is a fixture)

FAST = 2 ** 10


@pytest.fixture
def world(client, db, me):
    wipe_person(db, me)
    w = seed_world(db, me)
    yield w
    wipe_person(db, me)
    clean_files()


def export(db, me, keys, kind="export"):
    return read_archive(build_archive(db, kind=kind, uid=me, creator="Tester", keys=keys), max_bytes=50_000_000)


def run(db, me, archive, plan):
    return loader.load(db, archive, uid=me, username_key="tester", is_admin=True, plan=plan)


def rewrite(archive, entries=None, **manifest_changes):
    entries = entries if entries is not None else {n: archive.read(n) for n in archive.names()}
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")} | manifest_changes
    return read_archive(write_archive(manifest, entries), max_bytes=50_000_000)


# ---------------------------------------------------------------- paths

@pytest.mark.parametrize("name", ["files/C:/Users/Public/evil.txt", "files/c:evil", "files/coa/a:b", "files/coa/x\x00y",
                                  "files//double", "files/coa/ends-with-dot.", "files/coa/ trailing", "files/coa/a*b",
                                  "files/coa/a?b", "files/coa/a|b", "files/coa/con.txt", "../x", "/x", "a/../b"])
def test_entry_names_that_could_leave_the_data_folders_are_not_safe(name):
    assert not _safe(name)


@pytest.mark.parametrize("name", ["manifest-like.json", "sections/inventory.json", "persons/tester/labs.json",
                                  "files/coa/0123abcd.pdf", "files/library/cards/001.jpg", "files/library/cards.json"])
def test_ordinary_entry_names_are_safe(name):
    assert _safe(name)


def test_an_archive_with_a_drive_letter_entry_cannot_be_opened():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": 1, "entries": {"files/C:/evil.txt": "0" * 64}}))
        z.writestr("files/C:/evil.txt", b"x")
    with pytest.raises(BackupError, match="damaged"):
        read_archive(buffer.getvalue(), max_bytes=10_000)


def test_restore_never_keeps_a_stored_file_name_that_could_escape_its_folder(scratch):
    s, admin, tmp = scratch
    archive = whole(s, admin)
    entries = {n: archive.read(n) for n in archive.names()}
    key = next(n for n in entries if n.startswith("persons/admin/") and n.endswith("inventory.json"))
    payload = json.loads(entries[key])
    payload["tables"]["order_items"][0]["coa_filename"] = "../../scratch.db"
    entries[key] = json.dumps(payload).encode()
    restore.restore_installation(s, rewrite(archive, entries), uid=admin, creator="Admin", safety_passphrase=PASS)
    names = [r[0] for r in s.execute(text("SELECT coa_filename FROM order_items"))]
    assert None in names and not any(n and ("/" in n or ".." in n) for n in names)


# ---------------------------------------------------------------- ownership of natural-key links

def test_a_protocol_is_never_linked_to_someone_elses_inventory_item(client, db, me, world):
    archive = export(db, me, ["protocols"])
    db.execute(text("INSERT INTO users (username, username_key, password_hash, is_admin, totp_enabled, failed_attempts, created_at) "
                    "VALUES ('Neighbor', 'neighbor', 'x', 0, 0, 0, '2026-01-01')"))
    other = db.execute(text("SELECT id FROM users WHERE username_key = 'neighbor'")).scalar()
    db.add(InventoryItem(owner_id=other, name="Zorvex A 10mg", vial_size_mg=10.0, count=1))
    db.commit()
    try:
        wipe_person(db, me)                                    # my own item is gone, theirs has the same name and size
        run(db, me, archive, {"protocols": loader.ADD})
        assert db.execute(text("SELECT inventory_item_id FROM protocol_items")).scalar() is None
    finally:
        db.execute(text("DELETE FROM inventory_items WHERE owner_id = :o"), {"o": other})
        db.execute(text("DELETE FROM users WHERE id = :o"), {"o": other})
        db.commit()


# ---------------------------------------------------------------- errors are not hidden

def test_a_row_that_breaks_a_required_column_stops_the_load_and_changes_nothing(client, db, me, world):
    archive = export(db, me, ["measurements", "journal"])
    entries = {n: archive.read(n) for n in archive.names()}
    payload = json.loads(entries["sections/measurements.json"])
    payload["tables"]["body_measurements"][0].pop("measured_at")
    entries["sections/measurements.json"] = json.dumps(payload).encode()
    before = person_counts(db, me, ["measurements", "journal"])
    with pytest.raises(BackupError, match="body_measurements|required|nothing was changed"):
        run(db, me, rewrite(archive, entries), {"measurements": loader.REPLACE, "journal": loader.REPLACE})
    assert person_counts(db, me, ["measurements", "journal"]) == before


def test_a_row_that_duplicates_something_is_still_skipped_not_fatal(client, db, me, world):
    report = run(db, me, export(db, me, ["journal"]), {"journal": loader.ADD})
    assert report.skipped["journal"] >= 1


def test_restore_counts_the_rows_it_inserted_and_rejects_a_row_missing_a_required_column(scratch):
    s, admin, _ = scratch
    archive = whole(s, admin)
    report = restore.restore_installation(s, archive, uid=admin, creator="Admin", safety_passphrase=PASS)
    assert report.rows["order_items"] == 2 and report.rows["users"] == 2          # inserted, not merely present in the file
    entries = {n: archive.read(n) for n in archive.names()}
    key = next(n for n in entries if n.startswith("persons/admin/") and n.endswith("measurements.json"))
    payload = json.loads(entries[key])
    payload["tables"]["body_measurements"][0].pop("measured_at")
    entries[key] = json.dumps(payload).encode()
    before = snapshot(s)
    with pytest.raises(BackupError):
        restore.restore_installation(s, rewrite(archive, entries), uid=admin, creator="Admin", safety_passphrase=PASS)
    assert snapshot(s) == before


# ---------------------------------------------------------------- forged owners and malformed manifests

def test_a_forged_null_owner_still_lands_on_the_person_loading(client, db, me, world):
    archive = export(db, me, ["inventory", "journal"])
    entries = {n: archive.read(n) for n in archive.names()}
    for name in ("sections/inventory.json", "sections/journal.json"):
        payload = json.loads(entries[name])
        for rows in payload["tables"].values():
            for row in rows:
                if "owner_id" in row:
                    row["owner_id"] = None
        entries[name] = json.dumps(payload).encode()
    wipe_person(db, me)
    run(db, me, rewrite(archive, entries), {"inventory": loader.ADD, "journal": loader.ADD})
    assert db.execute(text("SELECT COUNT(*) FROM inventory_items WHERE owner_id IS NULL")).scalar() == 0
    assert db.execute(text("SELECT COUNT(*) FROM inventory_items WHERE owner_id = :u"), {"u": me}).scalar() == 1


def test_a_manifest_missing_fields_gives_a_message_not_a_server_error(client, db, me, world):
    archive = export(db, me, ["journal"])
    blob = write_archive({"kind": "backup", "level": "person"}, {n: archive.read(n) for n in archive.names()})
    page = client.post("/backup/open", files={"file": ("x.amidebackup", seal(blob, "pass phrase 1"), "application/octet-stream")},
                       data={"passphrase": "pass phrase 1"})
    assert page.status_code in (200, 422) and "Traceback" not in page.text


def test_restore_refuses_a_backup_with_no_accounts(scratch):
    s, admin, _ = scratch
    archive = whole(s, admin)
    entries = {n: archive.read(n) for n in archive.names()}
    payload = json.loads(entries["sections/accounts.json"])
    payload["tables"]["users"] = []
    entries["sections/accounts.json"] = json.dumps(payload).encode()
    before = snapshot(s)
    with pytest.raises(BackupError, match="no accounts"):
        restore.restore_installation(s, rewrite(archive, entries), uid=admin, creator="Admin", safety_passphrase=PASS)
    assert snapshot(s) == before


def test_two_safety_copies_in_the_same_second_never_overwrite_each_other(scratch):
    s, admin, tmp = scratch
    names = {restore.write_safety_backup(s, uid=admin, creator="Admin", passphrase=PASS).name for _ in range(3)}
    assert len(names) == 3


# ---------------------------------------------------------------- opened files and costs

def test_an_opened_backup_is_never_plaintext_on_disk_and_does_not_survive_a_restart(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    secret = b"PK\x03\x04 plaintext archive with password hashes"
    token = pending.put(7, secret)
    on_disk = b"".join(p.read_bytes() for p in (tmp_path / "backups" / ".pending").glob("*"))
    assert secret not in on_disk and b"password hashes" not in on_disk
    assert pending.get(7, token) == secret
    pending._KEYS.clear()                                  # as if the server restarted: the keys lived only in memory
    with pytest.raises(BackupError, match="expired"):
        pending.get(7, token)
    pending.purge(everything=True)
    assert not list((tmp_path / "backups" / ".pending").glob("*"))


def test_a_header_asking_for_a_large_scrypt_cost_is_refused_without_computing_it():
    blob = bytearray(seal(b"x", "correct horse", n=FAST))
    blob[8:12] = (2 ** 17).to_bytes(4, "big")
    with pytest.raises(BackupError, match="damaged"):
        unseal(bytes(blob), "correct horse")
    assert container._N_MAX < 2 ** 17


# ---------------------------------------------------------------- the folder swap

def test_a_failed_folder_swap_leaves_the_old_files_in_place_and_says_so(scratch, monkeypatch):
    s, admin, tmp = scratch
    archive = whole(s, admin)
    before_files = snapshot(s)[1]
    real_rename = Path.rename

    def flaky(self, target):
        if "backups" in str(self) and ".restore-" in str(self) and "uploads" in str(target):
            raise OSError("file in use")
        return real_rename(self, target)

    with monkeypatch.context() as patched:          # a context, so the scratch fixture's own patches stay in force
        patched.setattr(Path, "rename", flaky)
        with pytest.raises(BackupError, match="could not be put in place"):
            restore.restore_installation(s, archive, uid=admin, creator="Admin", safety_passphrase=PASS)
    assert snapshot(s)[1] == before_files
    assert all(getattr(config, k).is_dir() for k in ("COA_DIR", "PRICE_LIST_DIR", "LAB_REPORT_DIR", "WORKOUT_PDF_DIR", "WALLET_QR_DIR"))
