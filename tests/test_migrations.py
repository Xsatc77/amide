import sqlite3
from pathlib import Path

import pytest
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
        # The hardest part of this migration: SQLite reflection drops custom collations, so a naive
        # batch_alter_table recreate silently loses vendors.name's COLLATE NOCASE. Assert it survived.
        vendors_sql = c.execute("select sql from sqlite_master where name='vendors'").fetchone()[0]
        assert "COLLATE NOCASE" in vendors_sql.upper() or "COLLATE \"NOCASE\"" in vendors_sql.upper()
        rows = c.execute("select id, created_by_id, name from vendors").fetchall()
        assert len(rows) == 1  # deduped
        kept_id = rows[0][0]
        assert kept_id == 1  # the lowest id survives, per the dedupe rule
        assert rows[0][2].lower() == "acme peptides"
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into vendors(created_by_id,name,created_at) values (1,'ACME PEPTIDES','2026-09-24')")
        # Item B's vendor_id was repointed to the surviving row.
        assert c.execute("select vendor_id from inventory_items where name='Item B'").fetchone() == (kept_id,)
        shares_cols = {r[1] for r in c.execute("pragma table_info(shares)")}
        assert {"owner_id", "grantee_id", "category", "created_at"} <= shares_cols
    command.downgrade(cfg, "0008")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "shares" not in tables
        assert "owner_id" in {r[1] for r in c.execute("pragma table_info(vendors)")}


def test_0010_adds_active_vials_and_discard_days(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0009")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-25')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        assert "default_discard_days" in {r[1] for r in c.execute("pragma table_info(users)")}
        cols = {r[1] for r in c.execute("pragma table_info(active_vials)")}
        assert {"owner_id", "inventory_item_id", "concentration_mg_ml", "water_ml", "dose_value",
               "dose_unit", "doses_total", "date_mixed", "discard_by", "discarded_at",
               "last_discard_prompt_at", "created_at"} <= cols
    command.downgrade(cfg, "0009")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "active_vials" not in tables
        assert "default_discard_days" not in {r[1] for r in c.execute("pragma table_info(users)")}


def test_0011_adds_orders_and_categorizes_existing_items(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0010")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-25')")
        # A Lyophilized item with full legacy order/vendor/lot/COA data.
        c.execute("insert into inventory_items(name,count,vial_size_unit,medium,vendor,lot_number,"
                  "cost_cents,order_date,shipped_date,arrival_date,coa_vial_size_mg,coa_purity_pct,"
                  "created_at,updated_at,owner_id) values ('Retatrutide',10,'mg','Lyophilized',"
                  "'PeptideCo','LOT1',8400,'2026-08-01','2026-08-03','2026-08-10',9.48,99.5,"
                  "'2026-08-01','2026-08-01',1)")
        # A no-medium item (today's "supply" pattern), no order data at all.
        c.execute("insert into inventory_items(name,count,vial_size_unit,created_at,updated_at,owner_id)"
                  " values ('Alcohol Prep Pads',250,'mg','2026-09-01','2026-09-01',1)")
        # A fully-consumed Lyophilized item (count=0) that still carries vendor/lot/order-date
        # data -- a very common real case (bought it, used it all, purchase record remains).
        c.execute("insert into inventory_items(name,count,vial_size_unit,medium,vendor,lot_number,"
                  "cost_cents,order_date,created_at,updated_at,owner_id) values ('Tirzepatide',0,'mg',"
                  "'Lyophilized','PeptideCo','LOT2',7000,'2026-07-01','2026-07-01','2026-07-01',1)")
    command.upgrade(cfg, "0011")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(orders)")}
        assert {"inventory_item_id", "quantity", "order_date", "shipped_date", "arrival_date",
               "tracking_site", "tracking_number", "vendor", "vendor_id", "lot_number",
               "cost_cents", "tax_cents", "shipping_cents", "expiration_date", "coa_filename",
               "coa_vial_size_mg", "coa_purity_pct"} <= cols
        item_cols = {r[1] for r in c.execute("pragma table_info(inventory_items)")}
        assert {"category", "reconstituted_count", "sold_count"} <= item_cols
        assert "lot_number" not in item_cols and "arrival_date" not in item_cols

        cat, qty = c.execute("select category,count from inventory_items where name='Retatrutide'").fetchone()
        assert cat == "Medicine"
        order = c.execute("select quantity,vendor,lot_number,arrival_date from orders "
                          "where inventory_item_id=(select id from inventory_items where name='Retatrutide')").fetchone()
        assert order == (10, "PeptideCo", "LOT1", "2026-08-10")

        cat2 = c.execute("select category from inventory_items where name='Alcohol Prep Pads'").fetchone()[0]
        assert cat2 == "Supply"
        no_orders = c.execute("select count(*) from orders where inventory_item_id="
                              "(select id from inventory_items where name='Alcohol Prep Pads')").fetchone()[0]
        assert no_orders == 0

        # Fully-consumed item: a qty=1 Order had to be synthesized (quantity>0 constraint), but
        # reconstituted_count was bumped to match, so quantity - reconstituted_count == 0 -- no
        # phantom stock.
        tirz_id = c.execute("select id from inventory_items where name='Tirzepatide'").fetchone()[0]
        tirz_qty = c.execute("select quantity from orders where inventory_item_id=?", (tirz_id,)).fetchone()[0]
        tirz_reconstituted = c.execute(
            "select reconstituted_count from inventory_items where id=?", (tirz_id,)).fetchone()[0]
        assert tirz_qty == 1
        assert tirz_reconstituted == 1
        assert tirz_qty - tirz_reconstituted == 0
    command.downgrade(cfg, "0010")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "orders" not in tables
        assert "category" not in {r[1] for r in c.execute("pragma table_info(inventory_items)")}


def test_0012_adds_sales_table(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0011")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-25')")
        c.execute("insert into inventory_items(name,count,vial_size_unit,category,created_at,"
                  "updated_at,owner_id) values ('Retatrutide',0,'mg','Medicine','2026-09-25',"
                  "'2026-09-25',1)")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(sales)")}
        assert {"inventory_item_id", "quantity", "sale_date", "price_cents", "created_at"} <= cols
        item_id = c.execute("select id from inventory_items where name='Retatrutide'").fetchone()[0]
        c.execute("insert into sales(inventory_item_id,quantity,sale_date,price_cents,created_at) "
                  "values (?,2,'2026-09-25',15000,'2026-09-25')", (item_id,))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into sales(inventory_item_id,quantity,sale_date,price_cents,created_at) "
                      "values (?,0,'2026-09-25',100,'2026-09-25')", (item_id,))
    command.downgrade(cfg, "0011")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "sales" not in tables


def test_0013_splits_orders_into_order_items(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0012")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-25')")
        c.execute("insert into inventory_items(name,count,vial_size_unit,category,created_at,"
                  "updated_at,owner_id) values ('Retatrutide',0,'mg','Medicine','2026-09-25',"
                  "'2026-09-25',1)")
        item_id = c.execute("select id from inventory_items where name='Retatrutide'").fetchone()[0]
        # An arrived order (should backfill received_quantity = quantity).
        c.execute("insert into orders(inventory_item_id,quantity,order_date,arrival_date,cost_cents,"
                  "created_at) values (?,10,'2026-08-01','2026-08-10',8400,'2026-08-01')", (item_id,))
        # An in-transit order (should backfill received_quantity = NULL).
        c.execute("insert into orders(inventory_item_id,quantity,order_date,created_at) "
                  "values (?,5,'2026-09-20','2026-09-20')", (item_id,))
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(order_items)")}
        assert {"order_id", "inventory_item_id", "quantity", "received_quantity", "received_note",
               "cost_cents", "lot_number", "expiration_date", "coa_filename", "coa_vial_size_mg",
               "coa_purity_pct"} <= cols
        order_cols = {r[1] for r in c.execute("pragma table_info(orders)")}
        assert "inventory_item_id" not in order_cols and "quantity" not in order_cols
        assert {"order_date", "arrival_date", "tax_cents", "shipping_cents"} <= order_cols

        rows = c.execute("select quantity, received_quantity, cost_cents from order_items "
                         "order by quantity desc").fetchall()
        assert rows == [(10, 10, 8400), (5, None, None)]
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into order_items(order_id,inventory_item_id,quantity,received_quantity) "
                      "values (1,?,5,6)", (item_id,))  # received > ordered
    command.downgrade(cfg, "0012")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "order_items" not in tables
        assert "inventory_item_id" in {r[1] for r in c.execute("pragma table_info(orders)")}


def test_0014_adds_dose_logging(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0013")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-25')")
        c.execute("insert into inventory_items(name,count,vial_size_unit,category,created_at,"
                  "updated_at,owner_id) values ('Retatrutide',0,'mg','Medicine','2026-09-25',"
                  "'2026-09-25',1)")
        item_id = c.execute("select id from inventory_items where name='Retatrutide'").fetchone()[0]
        c.execute("insert into active_vials(owner_id,inventory_item_id,concentration_mg_ml,water_ml,"
                  "dose_value,dose_unit,doses_total,date_mixed,discard_by,created_at) values "
                  "(1,?,5.0,2.0,2.0,'mg',5,'2026-09-01','2026-09-29','2026-09-01')", (item_id,))
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(dose_logs)")}
        assert {"owner_id", "protocol_id", "protocol_item_id", "active_vial_id", "peptide_id",
               "peptide_name", "dose_value", "dose_unit", "route", "scheduled_date",
               "scheduled_time_of_day", "status", "logged_at", "injection_site", "volume_ml"} <= cols
        vial_cols = {r[1] for r in c.execute("pragma table_info(active_vials)")}
        assert {"dispensing_method", "volume_remaining_ml"} <= vial_cols
        method, remaining = c.execute(
            "select dispensing_method, volume_remaining_ml from active_vials").fetchone()
        assert method == "syringe" and remaining == 2.0  # backfilled from water_ml
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into dose_logs(owner_id,protocol_id,peptide_id,peptide_name,dose_unit,"
                      "route,scheduled_date,scheduled_time_of_day,status,volume_ml) values "
                      "(1,1,1,'X','mg','subq','2026-09-25','am','skipped',-1)")
    command.downgrade(cfg, "0013")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "dose_logs" not in tables
        vial_cols = {r[1] for r in c.execute("pragma table_info(active_vials)")}
        assert "dispensing_method" not in vial_cols and "volume_remaining_ml" not in vial_cols


def test_0015_adds_dashboard_thresholds(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0014")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-25')")
        c.execute("insert into inventory_items(name,count,vial_size_unit,category,created_at,"
                  "updated_at,owner_id) values ('Retatrutide',0,'mg','Medicine','2026-09-25',"
                  "'2026-09-25',1)")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        user_cols = {r[1] for r in c.execute("pragma table_info(users)")}
        assert {"low_stock_default", "shipment_delay_days"} <= user_cols
        item_cols = {r[1] for r in c.execute("pragma table_info(inventory_items)")}
        assert "low_stock_threshold" in item_cols
        # All three must be nullable (None means "use the default") -- explicitly set 0 must
        # round-trip as 0, never coerced to NULL or rejected.
        c.execute("update inventory_items set low_stock_threshold = 0 where name = 'Retatrutide'")
        value = c.execute(
            "select low_stock_threshold from inventory_items where name = 'Retatrutide'").fetchone()[0]
        assert value == 0
    command.downgrade(cfg, "0014")
    with sqlite3.connect(db) as c:
        user_cols = {r[1] for r in c.execute("pragma table_info(users)")}
        assert "low_stock_default" not in user_cols and "shipment_delay_days" not in user_cols
        item_cols = {r[1] for r in c.execute("pragma table_info(inventory_items)")}
        assert "low_stock_threshold" not in item_cols


def test_0016_adds_vendor_management(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0015")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-27')")
        c.execute("insert into vendors(name,contact_info,notes,created_at) values "
                  "('Acme Peptides','old-contact-info','existing notes','2026-09-27')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        # New tables exist.
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert {"contact_method_types", "payment_method_types", "vendor_contacts",
               "vendor_payment_methods", "vendor_favorites"} <= tables
        # Seeded defaults present.
        contact_names = {r[0] for r in c.execute("select name from contact_method_types")}
        assert {"Email", "WhatsApp", "Telegram", "Phone"} <= contact_names
        payment_names = {r[0] for r in c.execute("select name from payment_method_types")}
        assert {"Credit Card", "Cash", "Crypto", "Alibaba"} <= payment_names
        # Vendor gained the new columns and lost contact_info, with a backfill into notes.
        vendor_cols = {r[1] for r in c.execute("pragma table_info(vendors)")}
        assert {"supplier", "contact_name", "recommended", "price_list_filename",
               "price_list_url", "price_list_updated_at"} <= vendor_cols
        assert "contact_info" not in vendor_cols
        name, notes = c.execute(
            "select name, notes from vendors where name='Acme Peptides'").fetchone()
        assert "old-contact-info" in notes and "existing notes" in notes
    command.downgrade(cfg, "0015")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert not ({"contact_method_types", "payment_method_types", "vendor_contacts",
                    "vendor_payment_methods", "vendor_favorites"} & tables)
        vendor_cols = {r[1] for r in c.execute("pragma table_info(vendors)")}
        assert "contact_info" in vendor_cols
        assert "supplier" not in vendor_cols


def test_0017_adds_weight_measurements(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0016")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-28')")
        uid = c.execute("select id from users where username='A'").fetchone()[0]
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        user_cols = {r[1] for r in c.execute("pragma table_info(users)")}
        assert {"sex", "birth_date", "height_in", "activity_level", "macro_goal", "diet_preset",
               "custom_protein_pct", "custom_carb_pct", "custom_fat_pct", "water_goal_oz"} <= user_cols
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "body_measurements" in tables
        body_cols = {r[1] for r in c.execute("pragma table_info(body_measurements)")}
        assert {"owner_id", "measured_at", "weight_lbs", "systolic", "diastolic", "neck_in",
               "waist_in", "hips_in", "biceps_l_in", "biceps_r_in", "forearm_l_in", "forearm_r_in",
               "quad_l_in", "quad_r_in", "calf_l_in", "calf_r_in"} <= body_cols
        c.execute("insert into body_measurements(owner_id, measured_at, weight_lbs, created_at) "
                  "values (?, '2026-09-28', 180.5, '2026-09-28')", (uid,))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into body_measurements(owner_id, measured_at, weight_lbs, created_at) "
                      "values (?, '2026-09-28', -5, '2026-09-28')", (uid,))
    command.downgrade(cfg, "0016")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "body_measurements" not in tables
        user_cols = {r[1] for r in c.execute("pragma table_info(users)")}
        assert "sex" not in user_cols


def test_0018_adds_journal(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0017")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-28')")
        uid = c.execute("select id from users where username='A'").fetchone()[0]
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert {"journal_entries", "journal_entry_side_effects", "journal_quick_notes"} <= tables
        entry_cols = {r[1] for r in c.execute("pragma table_info(journal_entries)")}
        assert {"owner_id", "entry_date", "mood", "energy", "sleep_quality",
               "side_effects_other", "notes", "created_at"} <= entry_cols
        c.execute("insert into journal_entries(owner_id, entry_date, created_at) "
                  "values (?, '2026-09-28', '2026-09-28')", (uid,))
        entry_id = c.execute("select id from journal_entries where owner_id = ?", (uid,)).fetchone()[0]
        # unique (owner_id, entry_date) enforced
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into journal_entries(owner_id, entry_date, created_at) "
                      "values (?, '2026-09-28', '2026-09-28')", (uid,))
        # mood range constraint enforced
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into journal_entries(owner_id, entry_date, mood, created_at) "
                      "values (?, '2026-09-29', 6, '2026-09-28')", (uid,))
        c.execute("insert into journal_entry_side_effects(entry_id, side_effect) values (?, 'Headache')",
                  (entry_id,))
        c.execute("insert into journal_quick_notes(entry_id, noted_at, text) values (?, '2026-09-28T14:00:00', 'felt foggy')",
                  (entry_id,))
    command.downgrade(cfg, "0017")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert not ({"journal_entries", "journal_entry_side_effects", "journal_quick_notes"} & tables)


def test_0019_adds_labs(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0018")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-28')")
        uid = c.execute("select id from users where username='A'").fetchone()[0]
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert {"lab_panels", "lab_results"} <= tables
        panel_cols = {r[1] for r in c.execute("pragma table_info(lab_panels)")}
        assert {"owner_id", "drawn_at", "notes", "report_filename", "created_at"} <= panel_cols
        result_cols = {r[1] for r in c.execute("pragma table_info(lab_results)")}
        assert {"panel_id", "marker", "marker_other", "value", "unit", "range_low",
               "range_high"} <= result_cols
        c.execute("insert into lab_panels(owner_id, drawn_at, created_at) "
                  "values (?, '2026-09-28', '2026-09-28')", (uid,))
        panel_id = c.execute("select id from lab_panels where owner_id = ?", (uid,)).fetchone()[0]
        c.execute("insert into lab_results(panel_id, marker, value) values (?, 'TSH', 2.5)",
                  (panel_id,))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into lab_results(panel_id, marker, value, range_low, range_high) "
                      "values (?, 'TSH', 2.5, 5.0, 1.0)", (panel_id,))
    command.downgrade(cfg, "0018")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert not ({"lab_panels", "lab_results"} & tables)


def test_0020_adds_peptide_sheets(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0019")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        peptide_cols = {r[1] for r in c.execute("pragma table_info(peptides)")}
        assert {"half_life_text", "bioavailability_text", "tmax_text", "route_summary",
               "storage_before_text", "storage_after_text", "storage_temperature_text",
               "legal_status_text", "cost_estimate_text", "usage_tips", "sheet_sections"} <= peptide_cols
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert {"peptide_dosing_tiers", "peptide_cycles", "peptide_stack_relations",
               "peptide_monitoring_tests"} <= tables
        c.execute("insert into peptides(name, source) values ('Test-Compound-9', 'sheet')")
        pid = c.execute("select id from peptides where name='Test-Compound-9'").fetchone()[0]
        c.execute("insert into peptide_dosing_tiers(peptide_id, level, dose_text, frequency_text) "
                  "values (?, 'Beginner', '50mg', 'Daily')", (pid,))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into peptide_dosing_tiers(peptide_id, level, dose_text, frequency_text) "
                      "values (?, 'Beginner', '100mg', 'Daily')", (pid,))  # unique (peptide_id, level)
    command.downgrade(cfg, "0019")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert not ({"peptide_dosing_tiers", "peptide_cycles", "peptide_stack_relations",
                    "peptide_monitoring_tests"} & tables)
        peptide_cols = {r[1] for r in c.execute("pragma table_info(peptides)")}
        assert "half_life_text" not in peptide_cols


def test_0021_adds_tags_and_summary(tmp_path):
    db = tmp_path / "i.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0020")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        peptide_cols = {r[1] for r in c.execute("pragma table_info(peptides)")}
        assert {"tags", "summary"} <= peptide_cols
        # A plain nullable column add uses SQLite's native ALTER TABLE ADD COLUMN, not a
        # reflect-and-rebuild -- unlike 0020's/0016's raw-SQL rebuilds, this must NOT touch (and so
        # cannot drop) peptides.name's COLLATE NOCASE.
        peptides_sql = c.execute("select sql from sqlite_master where name='peptides'").fetchone()[0]
        assert "COLLATE NOCASE" in peptides_sql.upper()
        c.execute("insert into peptides(name, source, tags, summary) "
                  "values ('Test-Compound-9', 'sheet', '[\"Recovery\"]', 'A summary.')")
        row = c.execute("select tags, summary from peptides where name='Test-Compound-9'").fetchone()
        assert row == ('["Recovery"]', "A summary.")
    command.downgrade(cfg, "0020")
    with sqlite3.connect(db) as c:
        peptide_cols = {r[1] for r in c.execute("pragma table_info(peptides)")}
        assert not ({"tags", "summary"} & peptide_cols)


def test_0022_allows_null_dosing_and_monitoring_text(tmp_path):
    db = tmp_path / "j.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0021")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        c.execute("insert into peptides(name, source) values ('Test-Compound-9', 'sheet')")
        pid = c.execute("select id from peptides where name='Test-Compound-9'").fetchone()[0]
        # A real row missing a table cell must be storable with NULL, not raise IntegrityError.
        c.execute(
            "insert into peptide_dosing_tiers(peptide_id, level, dose_text, frequency_text) "
            "values (?, 'Beginner', NULL, NULL)", (pid,))
        c.execute(
            "insert into peptide_monitoring_tests(peptide_id, test_name, when_text, why_text) "
            "values (?, 'Made-up test', NULL, NULL)", (pid,))
        row = c.execute(
            "select dose_text, frequency_text from peptide_dosing_tiers where peptide_id=?", (pid,)
        ).fetchone()
        assert row == (None, None)
        row = c.execute(
            "select when_text, why_text from peptide_monitoring_tests where peptide_id=?", (pid,)
        ).fetchone()
        assert row == (None, None)
    command.downgrade(cfg, "0021")
    with sqlite3.connect(db) as c:
        cols = {r[1]: r[3] for r in c.execute("pragma table_info(peptide_dosing_tiers)")}
        assert cols["dose_text"] == 1 and cols["frequency_text"] == 1  # notnull flag restored
        cols = {r[1]: r[3] for r in c.execute("pragma table_info(peptide_monitoring_tests)")}
        assert cols["when_text"] == 1 and cols["why_text"] == 1


def test_0023_adds_sheet_sections_simple(tmp_path):
    db = tmp_path / "k.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0022")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        peptide_cols = {r[1] for r in c.execute("pragma table_info(peptides)")}
        assert "sheet_sections_simple" in peptide_cols
        # Plain nullable column add -- must not touch (and so cannot drop) name's COLLATE NOCASE.
        peptides_sql = c.execute("select sql from sqlite_master where name='peptides'").fetchone()[0]
        assert "COLLATE NOCASE" in peptides_sql.upper()
        c.execute("insert into peptides(name, source, sheet_sections_simple) "
                  "values ('Test-Compound-9', 'sheet', '{\"what_is\": \"Plain-language text.\"}')")
        row = c.execute(
            "select sheet_sections_simple from peptides where name='Test-Compound-9'"
        ).fetchone()
        assert row == ('{"what_is": "Plain-language text."}',)
    command.downgrade(cfg, "0022")
    with sqlite3.connect(db) as c:
        peptide_cols = {r[1] for r in c.execute("pragma table_info(peptides)")}
        assert "sheet_sections_simple" not in peptide_cols


def test_0024_creates_workout_tables(tmp_path):
    db = tmp_path / "l.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0023")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute(
            "select name from sqlite_master where type='table'")}
        for t in ("workout_plans", "workout_plan_days", "workout_exercises",
                  "workout_logs", "workout_exercise_logs", "fitness_test_results"):
            assert t in tables, t
        c.execute("insert into users(id, username, username_key, password_hash, is_admin, totp_enabled, failed_attempts, created_at) "
                  "values (1, 'tester', 'tester', 'x', 0, 0, 0, '2026-01-01')")
        c.execute(
            "insert into workout_plans(id, owner_id, name, source, started_on, created_at) "
            "values (1, 1, 'Test Plan', 'manual', '2026-01-01', '2026-01-01')")
        c.execute(
            "insert into workout_plan_days(id, plan_id, position, label) "
            "values (1, 1, 0, 'Day 1')")
        c.execute(
            "insert into workout_exercises(id, day_id, position, name) "
            "values (1, 1, 0, 'Push-up')")
        c.execute(
            "insert into workout_logs(id, owner_id, plan_day_id, log_date, completed_at) "
            "values (1, 1, 1, '2026-01-08', '2026-01-08')")
        c.execute(
            "insert into workout_exercise_logs(id, workout_log_id, exercise_id, completed) "
            "values (1, 1, 1, 1)")
        c.execute(
            "insert into fitness_test_results(id, owner_id, exercise, value, tested_at) "
            "values (1, 1, 'max_pushups', 20, '2026-01-01')")
    command.downgrade(cfg, "0023")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute(
            "select name from sqlite_master where type='table'")}
        for t in ("workout_plans", "workout_plan_days", "workout_exercises",
                  "workout_logs", "workout_exercise_logs", "fitness_test_results"):
            assert t not in tables, t


def test_0025_adds_heart_rate_bpm(tmp_path):
    db = tmp_path / "m.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0024")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-30')")
        uid = c.execute("select id from users where username='A'").fetchone()[0]
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        body_cols = {r[1] for r in c.execute("pragma table_info(body_measurements)")}
        assert "heart_rate_bpm" in body_cols
        c.execute("insert into body_measurements(owner_id, measured_at, heart_rate_bpm, created_at) "
                  "values (?, '2026-09-30', 62, '2026-09-30')", (uid,))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into body_measurements(owner_id, measured_at, heart_rate_bpm, created_at) "
                      "values (?, '2026-09-30', -1, '2026-09-30')", (uid,))
    command.downgrade(cfg, "0024")
    with sqlite3.connect(db) as c:
        body_cols = {r[1] for r in c.execute("pragma table_info(body_measurements)")}
        assert "heart_rate_bpm" not in body_cols


def test_0027_adds_protocol_item_cycle_offs(tmp_path):
    db = tmp_path / "n.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0026")
    with sqlite3.connect(db) as c:
        peptide_id = c.execute("select id from peptides where name='BPC-157'").fetchone()[0]
        c.execute("insert into protocols(id, name, start_date, paused, titration_enabled, created_at, updated_at) "
                  "values (1, 'Mine', '2026-09-22', 0, 0, '2026-09-22', '2026-09-22')")
        c.execute("insert into protocol_items(id, protocol_id, peptide_id, position, dose_unit, frequency, "
                  "time_of_day, route) values (1, 1, ?, 0, 'mg', 'weekly', 'any', 'subq')", (peptide_id,))
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "protocol_item_cycle_offs" in tables
        c.execute("insert into protocol_item_cycle_offs(protocol_item_id, start_week, end_week) "
                  "values (1, 4, 6)")
        assert c.execute("select start_week, end_week from protocol_item_cycle_offs").fetchone() == (4, 6)
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into protocol_item_cycle_offs(protocol_item_id, start_week, end_week) "
                      "values (1, 2, 1)")  # end_week < start_week
    command.downgrade(cfg, "0026")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "protocol_item_cycle_offs" not in tables
