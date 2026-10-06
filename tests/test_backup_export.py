from datetime import date

import pytest
from sqlalchemy import text

from app import config
from app.backup import sections as reg
from app.backup.archive import read_archive
from app.backup.container import BackupError
from app.backup.export import build_archive
from app.models import BodyMeasurement, InventoryItem
from backup_helpers import clean_files, seed_world, wipe_person

ALL_PERSON = list(reg.PERSON_SECTIONS)


@pytest.fixture
def world(client, db, me):
    wipe_person(db, me)
    w = seed_world(db, me)
    yield w
    wipe_person(db, me)
    clean_files()


def open_archive(data):
    return read_archive(data, max_bytes=50_000_000)


def backup(db, me, keys, kind="backup", **kw):
    return open_archive(build_archive(db, kind=kind, uid=me, creator="Tester", keys=keys, **kw))


def test_the_registry_covers_every_table_in_the_schema():
    assert reg.uncovered_tables() == set()


def test_every_link_leaving_a_section_is_resolved_by_name_or_deliberately_left_empty():
    deliberately_empty = {("dose_logs", "active_vial_id"),          # a vial belongs to Inventory; empty without it
                         ("ingest_items", "price_list_id")}          # a price list has no name to find it by; empty without Price lists
    for section in reg.SECTIONS.values():
        inside = {t.name for t in section.tables}
        for tbl in section.tables:
            for fk in reg.table(tbl.name).foreign_keys:
                target, col = fk.column.table.name, fk.parent.name
                assert target in inside or target == "users" or (tbl.name, col) in reg.REFS \
                    or (tbl.name, col) in deliberately_empty, (section.key, tbl.name, col)


def test_load_order_puts_shared_data_before_people_and_inventory_before_protocols():
    order = list(reg.LOAD_ORDER)
    assert order.index("vendors") < order.index("inventory") < order.index("protocols")
    assert order.index("library") < order.index("protocols") and "accounts" not in order


def test_a_backup_holds_only_the_chosen_sections_with_counts_and_files(client, db, me, world):
    archive = backup(db, me, ["inventory", "labs"])
    assert archive.manifest["kind"] == "backup" and archive.manifest["level"] == "person"
    assert set(archive.manifest["sections"]) == {"inventory", "labs"}
    assert archive.manifest["sections"]["inventory"]["rows"]["order_items"] == 1
    assert archive.has("sections/inventory.json") and not archive.has("sections/journal.json")
    names = archive.names()
    assert any(n.startswith("files/coa/") for n in names) and any(n.startswith("files/lab_reports/") for n in names)
    rows = archive.json("sections/inventory.json")["tables"]["inventory_items"]
    assert [r["name"] for r in rows] == ["Zorvex A 10mg"] and archive.manifest["revision"]


def test_the_profile_never_carries_credentials(client, db, me):
    archive = backup(db, me, ["profile"])
    row = archive.json("sections/profile.json")["tables"]["users"][0]
    assert set(row) <= set(reg.PROFILE_COLUMNS)
    blob = archive.read("sections/profile.json").decode()
    assert "password" not in blob and "totp" not in blob and "email" not in blob


def test_a_person_backup_never_contains_another_persons_rows(client, db, me, world):
    db.execute(text("INSERT INTO users (username, username_key, password_hash, is_admin, totp_enabled, failed_attempts, created_at) "
                    "VALUES ('Other', 'other', 'x', 0, 0, 0, '2026-01-01')"))
    other = db.execute(text("SELECT id FROM users WHERE username_key = 'other'")).scalar()
    db.add_all([InventoryItem(owner_id=other, name="Quillamine Private", count=1),
                BodyMeasurement(owner_id=other, measured_at=date(2026, 1, 1), weight_lbs=99.0)])
    db.commit()
    try:
        archive = backup(db, me, ALL_PERSON)
        blob = b"".join(archive.read(n) for n in archive.names() if n.endswith(".json"))
        assert b"Quillamine Private" not in blob and b'"weight_lbs":99' not in blob
    finally:
        db.execute(text("DELETE FROM inventory_items WHERE owner_id = :o"), {"o": other})
        db.execute(text("DELETE FROM body_measurements WHERE owner_id = :o"), {"o": other})
        db.execute(text("DELETE FROM users WHERE username_key = 'other'"))
        db.commit()


def test_rows_that_point_outside_their_section_carry_natural_keys(client, db, me, world):
    protocols = backup(db, me, ["protocols"]).json("sections/protocols.json")["tables"]
    item = protocols["protocol_items"][0]
    assert item["_refs"]["peptide_id"] == ["Zorvex A"]
    assert item["_refs"]["inventory_item_id"] == ["Zorvex A 10mg", 10.0, "mg"]
    inventory = backup(db, me, ["inventory"]).json("sections/inventory.json")["tables"]
    assert inventory["inventory_items"][0]["_refs"]["vendor_id"] == ["Acme Labs A"]
    both = backup(db, me, ["inventory", "protocols"]).json("sections/protocols.json")["tables"]
    assert both["protocol_items"][0]["_refs"]["peptide_id"] == ["Zorvex A"]


def test_a_share_file_strips_personal_records_wallets_and_attached_personal_files(client, db, me, world):
    archive = backup(db, me, list(reg.SHAREABLE), kind="share")
    assert archive.manifest["kind"] == "share"
    tables = {}
    for name in archive.names():
        if name.startswith("sections/"):
            tables.update(archive.json(name)["tables"])
    for dropped in ("dose_logs", "workout_logs", "workout_exercise_logs", "fitness_test_results", "sales", "active_vials",
                    "vendor_wallets", "vendor_favorites", "price_alert_ignores"):
        assert dropped not in tables, dropped
    for kept in ("inventory_items", "orders", "order_items", "protocols", "protocol_items", "workout_plans",
                 "workout_exercises", "vendors", "vendor_contacts", "price_lists", "price_list_items", "peptides"):
        assert kept in tables, kept
    assert all(v["created_by_id"] is None for v in tables["vendors"])
    assert all(p["source_pdf_filename"] is None for p in tables["workout_plans"])
    blob = b"".join(archive.read(n) for n in archive.names() if n.endswith(".json"))
    assert b"bc1q" not in blob and b"vendor_wallets" not in blob
    assert not any(n.startswith("files/wallet_qr/") or n.startswith("files/workout_pdfs/") for n in archive.names())
    assert any(n.startswith("files/coa/") for n in archive.names())


@pytest.mark.parametrize("keys", [["journal"], ["labs"], ["measurements"], ["profile"], ["inventory", "journal"]])
def test_personal_sections_cannot_go_in_a_share_file(client, db, me, keys):
    with pytest.raises(BackupError, match="cannot be put in a share file"):
        build_archive(db, kind="share", uid=me, creator="Tester", keys=keys)


@pytest.mark.parametrize("keys,message", [([], "at least one"), (["nonsense"], "Unknown section"), (["accounts"], "Unknown section")])
def test_bad_section_lists_are_refused(client, db, me, keys, message):
    with pytest.raises(BackupError, match=message):
        build_archive(db, kind="backup", uid=me, creator="Tester", keys=keys)


def test_an_unknown_kind_is_refused(client, db, me):
    with pytest.raises(BackupError, match="Unknown kind"):
        build_archive(db, kind="mystery", uid=me, creator="Tester", keys=["journal"])


def test_a_whole_installation_backup_has_every_person_every_shared_section_accounts_and_files(client, db, me, world):
    archive = open_archive(build_archive(db, kind="backup", uid=me, creator="Tester", keys=[], installation=True))
    assert archive.manifest["level"] == "installation" and archive.manifest["persons"]
    mine = next(p for p in archive.manifest["persons"] if p["id"] == me)["username_key"]
    assert archive.has(f"persons/{mine}/inventory.json") and archive.has("sections/accounts.json")
    assert archive.has("sections/vendors.json") and archive.has("sections/library.json")
    accounts = archive.json("sections/accounts.json")["tables"]["users"]
    assert any(u["password_hash"] for u in accounts)                 # whole-installation backups do carry credentials
    assert not any("sessions" in n for n in archive.names())
    assert any(n.startswith("files/wallet_qr/") for n in archive.names())


def test_a_whole_installation_file_cannot_be_a_share_file(client, db, me):
    with pytest.raises(BackupError, match="cannot be a share"):
        build_archive(db, kind="share", uid=me, creator="Tester", keys=[], installation=True)


def test_library_cards_travel_with_the_library_section(client, db, me, world):
    config.CARDS_DIR.mkdir(parents=True, exist_ok=True)
    (config.CARDS_DIR / "001.jpg").write_bytes(b"\xff\xd8\xff card")
    config.CARDS_JSON.write_text("[]", encoding="utf-8")
    try:
        archive = backup(db, me, ["library"])
        assert archive.has("files/library/cards/001.jpg") and archive.has("files/library/cards.json")
    finally:
        (config.CARDS_DIR / "001.jpg").unlink()
        config.CARDS_JSON.unlink()


def test_a_missing_attached_file_is_noted_not_fatal(client, db, me, world):
    (config.LAB_REPORT_DIR / world["peptide"].name).unlink(missing_ok=True)
    for f in config.LAB_REPORT_DIR.glob("*"):
        f.unlink()
    archive = backup(db, me, ["labs"])
    assert archive.manifest["missing_files"] and archive.json("sections/labs.json")["tables"]["lab_panels"]
