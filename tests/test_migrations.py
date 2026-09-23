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
