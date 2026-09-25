import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


def _cfg(db: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    cfg.attributes["configure_logger"] = False
    return cfg


def test_upgrade_from_0002_keeps_inventory_and_seeds(tmp_path):
    db = tmp_path / "a.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0002")
    with sqlite3.connect(db) as c:
        c.execute("insert into inventory_items(name,count,created_at,updated_at) values('Old',2,'2026-09-22','2026-09-22')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        assert c.execute("select name from inventory_items").fetchall() == [("Old",)]
        assert c.execute("select count(*) from peptides where source='card'").fetchone()[0] == 100
        assert c.execute("select count(*) from peptides where source='starter'").fetchone()[0] == 5
        assert c.execute("select card_number from peptides where name='BPC-157'").fetchone()[0] == 2
        goals = {g for (g,) in c.execute("select distinct goal from goal_peptides")}
        assert len(goals) == 8
        first = c.execute(
            "select p.name from goal_peptides g join peptides p on p.id=g.peptide_id "
            "where g.goal='fat-loss' order by g.position").fetchall()
        assert [n for (n,) in first] == ["Retatrutide", "Tirzepatide", "Tesamorelin", "AOD-9604", "MOTS-c"]


def test_downgrade_to_0002(tmp_path):
    db = tmp_path / "b.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0002")
    with sqlite3.connect(db) as c:
        tables = {t for (t,) in c.execute("select name from sqlite_master where type='table'")}
    assert "protocols" not in tables and "peptides" not in tables and "inventory_items" in tables


def test_0004_adds_card_columns_and_keeps_data(tmp_path):
    db = tmp_path / "c.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0003")
    with sqlite3.connect(db) as c:
        c.execute("update peptides set notes='keep me' where name='BPC-157'")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(peptides)")}
        assert {"card_class", "category", "evidence_level", "status", "card_details", "card_image"} <= cols
        assert c.execute("select notes from peptides where name='BPC-157'").fetchone()[0] == "keep me"
    command.downgrade(cfg, "0003")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(peptides)")}
    assert "card_details" not in cols


def test_0005_repairs_missing_seed_and_keeps_owner_peptide(tmp_path):
    """A database that got the 0003 tables without the seed (e.g. migrated mid-development) is repaired;
    a peptide the owner already added under a card name is adopted, not duplicated."""
    db = tmp_path / "d.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0004")
    with sqlite3.connect(db) as c:
        c.execute("delete from goal_peptides")
        c.execute("delete from peptides")
        c.execute("insert into peptides(id, name, source) values (7, 'Retatrutide', 'custom')")
        c.execute("insert into protocols(id, name, start_date, paused, titration_enabled, created_at, updated_at) "
                  "values (1, 'Mine', '2026-09-22', 0, 0, '2026-09-22', '2026-09-22')")
        c.execute("insert into protocol_items(protocol_id, peptide_id, position, dose_unit, frequency, time_of_day, route) "
                  "values (1, 7, 0, 'mg', 'weekly', 'any', 'subq')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from peptides").fetchone()[0] == 105
        assert c.execute("select card_number, source from peptides where id=7").fetchone() == (43, "card")
        assert c.execute("select count(*) from peptides where lower(name)='retatrutide'").fetchone()[0] == 1
        assert c.execute("select peptide_id from protocol_items").fetchone()[0] == 7
        assert len({g for (g,) in c.execute("select distinct goal from goal_peptides")}) == 8
        fat = [n for (n,) in c.execute("select p.name from goal_peptides g join peptides p on p.id=g.peptide_id "
                                       "where g.goal='fat-loss' order by g.position")]
        assert fat == ["Retatrutide", "Tirzepatide", "Tesamorelin", "AOD-9604", "MOTS-c"]


def test_0005_is_a_no_op_on_complete_database(tmp_path):
    db = tmp_path / "e.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0004")
    with sqlite3.connect(db) as c:
        c.execute("delete from goal_peptides where goal='wellness'")  # owner removed a whole stack on purpose
        before = c.execute("select count(*) from peptides").fetchone()[0]
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from peptides").fetchone()[0] == before
        assert c.execute("select count(*) from goal_peptides where goal='wellness'").fetchone()[0] == 0


def test_0006_adds_users_sessions_and_owners(tmp_path):
    db = tmp_path / "f.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0005")
    with sqlite3.connect(db) as c:
        c.execute("insert into inventory_items(name,count,created_at,updated_at) values('Keep',1,'2026-09-23','2026-09-23')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        tables = {t for (t,) in c.execute("select name from sqlite_master where type='table'")}
        assert {"users", "sessions"} <= tables
        assert "owner_id" in {r[1] for r in c.execute("pragma table_info(inventory_items)")}
        assert "owner_id" in {r[1] for r in c.execute("pragma table_info(protocols)")}
        assert c.execute("select name, owner_id from inventory_items").fetchall() == [("Keep", None)]
    command.downgrade(cfg, "0005")
    with sqlite3.connect(db) as c:
        tables = {t for (t,) in c.execute("select name from sqlite_master where type='table'")}
        assert "users" not in tables
        assert "owner_id" not in {r[1] for r in c.execute("pragma table_info(inventory_items)")}


def test_0007_backfills_existing_rows_and_adds_new_columns(tmp_path):
    db = tmp_path / "g.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0006")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,failed_attempts,"
                  "created_at) values ('A','a','x',0,0,0,'2026-09-23')")
        c.execute("insert into inventory_items(name,count,vial_size_mg,owner_id,created_at,updated_at) "
                  "values ('Old',2,10,1,'2026-09-23','2026-09-23')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        tables = {t for (t,) in c.execute("select name from sqlite_master where type='table'")}
        assert "vendors" in tables
        cols = {r[1] for r in c.execute("pragma table_info(inventory_items)")}
        assert {"vial_size_unit", "volume_ml", "units_per_package", "expiration_date", "storage",
                "vendor_id"} <= cols
        row = c.execute("select name, vial_size_unit, volume_ml, storage, vendor_id from inventory_items "
                        "where name='Old'").fetchone()
        assert row == ("Old", "mg", None, None, None)
    command.downgrade(cfg, "0006")
    with sqlite3.connect(db) as c:
        tables = {t for (t,) in c.execute("select name from sqlite_master where type='table'")}
        assert "vendors" not in tables
        assert "vial_size_unit" not in {r[1] for r in c.execute("pragma table_info(inventory_items)")}


def test_0008_adds_settings_columns(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0007")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-24')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(users)")}
        assert {"email", "timezone", "colorway"} <= cols
        row = c.execute("select email, timezone, colorway from users where username='A'").fetchone()
        assert row == (None, None, None)
    command.downgrade(cfg, "0007")
    with sqlite3.connect(db) as c:
        assert "colorway" not in {r[1] for r in c.execute("pragma table_info(users)")}


def test_0009_adds_shares_and_makes_vendors_shared(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0008")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-24')")
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('B','b','x',0,0,0,'2026-09-24')")
        # Two different owners, same vendor name (case-different) -- the old schema allowed this.
        c.execute("insert into vendors(owner_id,name,created_at) values (1,'Acme Peptides','2026-09-24')")
        c.execute("insert into vendors(owner_id,name,created_at) values (2,'acme peptides','2026-09-24')")
        c.execute("insert into inventory_items(name,count,vial_size_unit,vendor_id,created_at,updated_at,owner_id)"
                  " values ('Item B',1,'mg',2,'2026-09-24','2026-09-24',2)")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(vendors)")}
        assert "created_by_id" in cols and "owner_id" not in cols
        rows = c.execute("select id, created_by_id, name from vendors").fetchall()
        assert len(rows) == 1  # deduped
        kept_id = rows[0][0]
        assert rows[0][2].lower() == "acme peptides"
        # Item B's vendor_id was repointed to the surviving row.
        assert c.execute("select vendor_id from inventory_items where name='Item B'").fetchone() == (kept_id,)
        shares_cols = {r[1] for r in c.execute("pragma table_info(shares)")}
        assert {"owner_id", "grantee_id", "category", "created_at"} <= shares_cols
    command.downgrade(cfg, "0008")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "shares" not in tables
        assert "owner_id" in {r[1] for r in c.execute("pragma table_info(vendors)")}
