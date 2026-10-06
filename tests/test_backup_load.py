import json
import re

import pytest
from sqlalchemy import text

from app import config
from app.backup import load as loader
from app.backup import sections as reg
from app.backup.archive import read_archive, write_archive
from app.backup.container import BackupError
from app.backup.export import build_archive
from backup_helpers import clean_files, person_counts, seed_world, wipe_person

PERSON = list(reg.PERSON_SECTIONS)
UNIQUE_BY_DAY = {"journal_entries", "journal_entry_side_effects", "journal_quick_notes"}   # one entry per person per day


@pytest.fixture
def world(client, db, me):
    wipe_person(db, me)
    w = seed_world(db, me)
    yield w
    wipe_person(db, me)
    clean_files()


def export(db, me, keys, kind="export"):
    return read_archive(build_archive(db, kind=kind, uid=me, creator="Tester", keys=keys), max_bytes=50_000_000)


def run(db, me, archive, plan, *, admin=True, username_key="tester"):
    return loader.load(db, archive, uid=me, username_key=username_key, is_admin=admin, plan=plan)


def add_all(keys):
    return {k: loader.REPLACE if k == "profile" else loader.ADD for k in keys}


def retag(archive, **changes):
    """The same archive with manifest fields changed (to test version handling)."""
    entries = {n: archive.read(n) for n in archive.names()}
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")}
    manifest.update(changes)
    return read_archive(write_archive(manifest, entries), max_bytes=50_000_000)


def test_an_export_loads_into_an_empty_install_and_gives_the_same_data(client, db, me, world):
    keys = PERSON + ["vendors", "price_lists"]
    before = person_counts(db, me, keys)
    archive = export(db, me, keys)
    wipe_person(db, me)
    clean_files()
    assert person_counts(db, me, ["inventory"])["inventory_items"] == 0
    report = run(db, me, archive, add_all(keys))
    assert person_counts(db, me, keys) == before
    assert report.removed == {} and report.unresolved == {} and report.files == 5
    item = db.execute(text("SELECT id, vendor_id, name FROM inventory_items WHERE owner_id = :u"), {"u": me}).one()
    assert db.execute(text("SELECT name FROM vendors WHERE id = :v"), {"v": item.vendor_id}).scalar() == "Acme Labs A"
    link = db.execute(text("SELECT inventory_item_id, peptide_id FROM protocol_items")).one()
    assert link.inventory_item_id == item.id
    assert db.execute(text("SELECT name FROM peptides WHERE id = :p"), {"p": link.peptide_id}).scalar() == "Zorvex A"
    vial = db.execute(text("SELECT active_vial_id, protocol_item_id FROM dose_logs")).one()
    assert vial.active_vial_id and vial.protocol_item_id
    plist = db.execute(text("SELECT vendor_id FROM price_lists")).scalar()
    assert db.execute(text("SELECT name FROM vendors WHERE id = :v"), {"v": plist}).scalar() == "Acme Labs A"
    log = db.execute(text("SELECT plan_day_id FROM workout_logs")).scalar()
    assert db.execute(text("SELECT label FROM workout_plan_days WHERE id = :d"), {"d": log}).scalar() == "Day A"
    name = db.execute(text("SELECT coa_filename FROM order_items")).scalar()
    assert name and (config.COA_DIR / name).read_bytes().startswith(b"%PDF")


def test_every_owner_column_becomes_the_person_loading_whatever_the_file_says(client, db, me, world):
    archive = export(db, me, ["journal", "measurements"])
    entries = {n: archive.read(n) for n in archive.names()}
    for name in ("sections/journal.json", "sections/measurements.json"):
        payload = json.loads(entries[name])
        for rows in payload["tables"].values():
            for row in rows:
                if "owner_id" in row:
                    row["owner_id"] = 987654
        entries[name] = json.dumps(payload).encode()
    forged = read_archive(write_archive({k: v for k, v in archive.manifest.items() if k not in ("format", "entries")}, entries),
                          max_bytes=50_000_000)
    wipe_person(db, me)
    run(db, me, forged, add_all(["journal", "measurements"]))
    assert db.execute(text("SELECT COUNT(*) FROM journal_entries WHERE owner_id = 987654")).scalar() == 0
    assert db.execute(text("SELECT COUNT(*) FROM journal_entries WHERE owner_id = :u"), {"u": me}).scalar() == 1


def test_add_keeps_what_is_there_skips_duplicates_and_merges_shared_data_by_name(client, db, me, world):
    keys = ["inventory", "journal", "vendors"]
    before = person_counts(db, me, keys)
    report = run(db, me, export(db, me, keys), add_all(keys))
    after = person_counts(db, me, keys)
    assert after["inventory_items"] == before["inventory_items"] + 1 and after["order_items"] == before["order_items"] + 1
    assert after["journal_entries"] == before["journal_entries"]                  # one entry per day: the copy is skipped
    assert after["vendors"] == before["vendors"] and after["vendor_contacts"] == before["vendor_contacts"]
    assert report.skipped["journal"] >= 1 and report.skipped["vendors"] >= 1 and report.added["inventory"] > 0


def test_replace_makes_the_section_match_the_file_and_removes_what_it_replaced(client, db, me, world):
    archive = export(db, me, ["inventory"])
    db.execute(text("UPDATE inventory_items SET count = 99 WHERE owner_id = :u"), {"u": me})
    db.execute(text("INSERT INTO inventory_items (owner_id, name, count, created_at, updated_at) "
                    "VALUES (:u, 'Extra Item', 1, '2026-01-01', '2026-01-01')"), {"u": me})
    db.commit()
    old_file = db.execute(text("SELECT coa_filename FROM order_items")).scalar()
    report = run(db, me, archive, {"inventory": loader.REPLACE})
    rows = db.execute(text("SELECT name, count FROM inventory_items WHERE owner_id = :u"), {"u": me}).all()
    assert [(r.name, r.count) for r in rows] == [("Zorvex A 10mg", 5)]
    assert report.removed["inventory"] >= 4 and report.added["inventory"] >= 4
    assert not (config.COA_DIR / old_file).exists()                             # the replaced COA file was removed
    assert (config.COA_DIR / db.execute(text("SELECT coa_filename FROM order_items")).scalar()).exists()


def test_replacing_inventory_alone_clears_links_into_it_and_replacing_both_restores_them(client, db, me, world):
    archive = export(db, me, ["inventory", "protocols"])
    run(db, me, archive, {"inventory": loader.REPLACE})
    assert db.execute(text("SELECT inventory_item_id FROM protocol_items")).scalar() is None
    run(db, me, archive, {"inventory": loader.REPLACE, "protocols": loader.REPLACE})
    item = db.execute(text("SELECT id FROM inventory_items WHERE owner_id = :u"), {"u": me}).scalar()
    assert db.execute(text("SELECT inventory_item_id FROM protocol_items")).scalar() == item


def test_a_failure_part_way_changes_nothing_and_removes_the_files_it_wrote(client, db, me, world, monkeypatch):
    archive = export(db, me, ["inventory", "journal"])
    before = person_counts(db, me, ["inventory", "journal"])
    files_before = sorted(p.name for p in config.COA_DIR.glob("*"))
    real, calls = loader._insert_row, {"n": 0}

    def flaky(ctx, section, tbl, row):
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("disk full")
        return real(ctx, section, tbl, row)

    monkeypatch.setattr(loader, "_insert_row", flaky)
    with pytest.raises(BackupError, match="nothing was changed"):
        run(db, me, archive, {"inventory": loader.REPLACE, "journal": loader.REPLACE})
    monkeypatch.undo()
    assert person_counts(db, me, ["inventory", "journal"]) == before
    assert sorted(p.name for p in config.COA_DIR.glob("*")) == files_before


def test_loading_one_section_leaves_the_rest_alone(client, db, me, world):
    archive = export(db, me, PERSON)
    before = person_counts(db, me, PERSON)
    run(db, me, archive, {"workouts": loader.REPLACE})
    assert person_counts(db, me, PERSON) == before


def test_shared_sections_need_an_administrator_and_can_only_be_added(client, db, me, world):
    archive = export(db, me, ["vendors", "inventory"])
    with pytest.raises(BackupError, match="administrator"):
        run(db, me, archive, {"vendors": loader.ADD}, admin=False)
    with pytest.raises(BackupError, match="can only be loaded as"):
        run(db, me, archive, {"vendors": loader.REPLACE})
    run(db, me, archive, {"inventory": loader.ADD}, admin=False)               # people load their own sections freely


def test_a_share_file_can_only_be_added_and_loads_into_an_empty_install(client, db, me, world):
    keys = list(reg.SHAREABLE)
    archive = export(db, me, keys, kind="share")
    with pytest.raises(BackupError, match="can only be loaded as"):
        run(db, me, archive, {"inventory": loader.REPLACE})
    wipe_person(db, me)
    clean_files()
    report = run(db, me, archive, {k: loader.ADD for k in ("vendors", "price_lists", "inventory", "protocols", "workouts")})
    counts = person_counts(db, me, ["inventory", "protocols", "workouts", "vendors"])
    assert counts["inventory_items"] == 1 and counts["protocol_items"] == 1 and counts["workout_plans"] == 1
    assert counts["vendors"] == 1 and counts["vendor_wallets"] == 0 and counts["workout_logs"] == 0 and counts["dose_logs"] == 0
    assert report.unresolved == {}


def test_a_file_from_a_newer_version_is_refused_and_an_older_one_loads_with_defaults(client, db, me, world):
    archive = export(db, me, ["workouts"])
    with pytest.raises(BackupError, match="newer version"):
        run(db, me, retag(archive, revision="9999"), {"workouts": loader.ADD})
    entries = {n: archive.read(n) for n in archive.names()}
    payload = json.loads(entries["sections/workouts.json"])
    for row in payload["tables"]["workout_exercise_logs"]:
        row.pop("net_kcal"), row.pop("compendium_code")                          # columns an older schema did not have
    entries["sections/workouts.json"] = json.dumps(payload).encode()
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")} | {"revision": "0001"}
    older = read_archive(write_archive(manifest, entries), max_bytes=50_000_000)
    run(db, me, older, {"workouts": loader.REPLACE})
    assert db.execute(text("SELECT net_kcal FROM workout_exercise_logs")).scalar() is None


def test_links_that_cannot_be_found_become_empty_and_are_reported_and_missing_peptides_are_created(client, db, me, world):
    archive = export(db, me, ["protocols"])
    wipe_person(db, me)
    report = run(db, me, archive, {"protocols": loader.ADD})
    assert db.execute(text("SELECT inventory_item_id FROM protocol_items")).scalar() is None
    assert report.unresolved.get("protocol_items.inventory_item_id") == 1 and report.unresolved.get("dose_logs.active_vial_id") == 1
    peptide = db.execute(text("SELECT p.name, p.source FROM protocol_items i JOIN peptides p ON p.id = i.peptide_id")).one()
    assert (peptide.name, peptide.source) == ("Zorvex A", "custom")


def test_the_profile_is_applied_to_the_signed_in_person_only(client, db, me, world):
    db.execute(text("UPDATE users SET height_in = 70, water_goal_oz = 90 WHERE id = :u"), {"u": me})
    db.commit()
    archive = export(db, me, ["profile"])
    db.execute(text("UPDATE users SET height_in = 60, water_goal_oz = NULL WHERE id = :u"), {"u": me})
    db.commit()
    report = run(db, me, archive, {"profile": loader.REPLACE})
    row = db.execute(text("SELECT height_in, water_goal_oz, password_hash FROM users WHERE id = :u"), {"u": me}).one()
    assert (row.height_in, row.water_goal_oz) == (70, 90) and row.password_hash and report.added["profile"] == 1
    db.execute(text("UPDATE users SET height_in = NULL, water_goal_oz = NULL WHERE id = :u"), {"u": me})
    db.commit()


def test_bad_plans_are_refused_before_anything_changes(client, db, me, world):
    archive = export(db, me, ["journal"])
    for plan, message in (({}, "at least one"), ({"inventory": loader.ADD}, "not in this backup"),
                          ({"journal": "merge"}, "can only be loaded as"), ({"accounts": loader.ADD}, "not in this backup")):
        with pytest.raises(BackupError, match=message):
            run(db, me, archive, plan)


def test_describe_lists_sections_with_what_this_person_may_do(client, db, me, world):
    archive = export(db, me, ["journal", "vendors", "profile"])
    mine = {d["key"]: d for d in loader.describe(archive, username_key="tester", is_admin=False)}
    assert mine["journal"]["modes"] == ["add", "replace"] and mine["journal"]["rows"] == 3
    assert mine["vendors"]["allowed"] is False and mine["vendors"]["modes"] == []
    assert mine["profile"]["modes"] == ["replace"]
    admin = {d["key"]: d for d in loader.describe(archive, username_key="tester", is_admin=True)}
    assert admin["vendors"]["modes"] == ["add"]


def test_loading_from_a_whole_installation_file_takes_only_this_persons_partition(client, db, me, world):
    archive = read_archive(build_archive(db, kind="backup", uid=me, creator="Tester", keys=[], installation=True),
                           max_bytes=50_000_000)
    listed = {d["key"] for d in loader.describe(archive, username_key="tester", is_admin=True)}
    assert {"inventory", "journal", "vendors", "library"} <= listed and "accounts" not in listed
    nobody = loader.describe(archive, username_key="nobody", is_admin=False)
    assert all(not d["allowed"] for d in nobody if d["level"] == reg.PERSON)
    wipe_person(db, me)
    run(db, me, archive, {"journal": loader.ADD, "inventory": loader.ADD, "vendors": loader.ADD})
    assert person_counts(db, me, ["journal"])["journal_entries"] == 1


def test_library_files_are_added_without_overwriting_existing_ones(client, db, me, world):
    config.CARDS_DIR.mkdir(parents=True, exist_ok=True)
    (config.CARDS_DIR / "001.jpg").write_bytes(b"original")
    config.CARDS_JSON.write_text("[]", encoding="utf-8")
    try:
        archive = export(db, me, ["library"])
        (config.CARDS_DIR / "001.jpg").write_bytes(b"local edit")
        (config.CARDS_DIR / "002.jpg").unlink(missing_ok=True)
        run(db, me, archive, {"library": loader.ADD})
        assert (config.CARDS_DIR / "001.jpg").read_bytes() == b"local edit"
    finally:
        (config.CARDS_DIR / "001.jpg").unlink(missing_ok=True)
        config.CARDS_JSON.unlink(missing_ok=True)


def test_attached_files_are_stored_under_new_names_with_a_safe_extension(client, db, me, world):
    archive = export(db, me, ["inventory"])
    entries = {n: archive.read(n) for n in archive.names()}
    old_name = db.execute(text("SELECT coa_filename FROM order_items")).scalar()
    payload = json.loads(entries["sections/inventory.json"])
    payload["tables"]["order_items"][0]["coa_filename"] = "weird name.b c"
    entries["sections/inventory.json"] = json.dumps(payload).encode()
    entries["files/coa/weird name.b c"] = entries.pop(f"files/coa/{old_name}")
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")}
    run(db, me, read_archive(write_archive(manifest, entries), max_bytes=50_000_000), {"inventory": loader.REPLACE})
    stored = db.execute(text("SELECT coa_filename FROM order_items")).scalar()
    assert re.fullmatch(r"[0-9a-f]{32}", stored) and (config.COA_DIR / stored).is_file()


def test_describe_says_how_many_current_rows_replace_would_delete(client, db, me, world):
    archive = export(db, me, ["inventory", "journal", "profile", "vendors"])
    listed = {d["key"]: d for d in loader.describe(archive, username_key="tester", is_admin=True, session=db, uid=me)}
    assert listed["inventory"]["current"] == 5 and listed["journal"]["current"] == 3
    assert listed["profile"]["current"] is None and listed["vendors"]["current"] is None
    assert all(d["current"] is None for d in loader.describe(archive, username_key="tester", is_admin=True))
