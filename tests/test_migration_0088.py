"""Migration 0088: an item holding several order lines becomes one item per line."""
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


def cfg(db: Path) -> Config:
    c = Config(str(ROOT / "alembic.ini"))
    c.set_main_option("script_location", str(ROOT / "migrations"))
    c.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    c.attributes["configure_logger"] = False
    return c


def _item(c, name, category="Medicine", reconstituted=0):
    c.execute("insert into inventory_items (name, count, vial_size_unit, purchasing_unit, medium, category, reconstituted_count, sold_count, "
              "dosed_units, is_active, local_seller, vial_size_mg, created_at, updated_at) "
              "values (?, 1, 'mg', 'individual', 'Drops', ?, ?, 0, 0, 1, 0, 5, '2026-10-01', '2026-10-01')", (name, category, reconstituted))
    return c.execute("select max(id) from inventory_items").fetchone()[0]


def _line(c, item_id, qty):
    c.execute("insert into orders (order_date, created_at) values ('2026-10-01', '2026-10-01')")
    oid = c.execute("select max(id) from orders").fetchone()[0]
    c.execute("insert into order_items (order_id, inventory_item_id, quantity, received_quantity, stock_applied, damaged_quantity) values (?, ?, ?, ?, 0, 0)",
              (oid, item_id, qty, qty))
    return c.execute("select max(id) from order_items").fetchone()[0]


def test_extra_lines_move_to_their_own_items_and_the_first_keeps_its_use(tmp_path):
    db = tmp_path / "m.db"
    command.upgrade(cfg(db), "0087")
    with sqlite3.connect(db) as c:
        b12 = _item(c, "B-12", reconstituted=2)
        first, second, third = _line(c, b12, 60), _line(c, b12, 10), _line(c, b12, 5)
        solo = _item(c, "Solo")
        _line(c, solo, 3)
        supply = _item(c, "Pads", category="Supply")
        _line(c, supply, 1); _line(c, supply, 1)
    command.upgrade(cfg(db), "0088")
    with sqlite3.connect(db) as c:
        owners = {r[0]: r[1] for r in c.execute("select id, inventory_item_id from order_items")}
        assert owners[first] == b12 and owners[second] != b12 and owners[third] not in (b12, owners[second])
        for line in (second, third):
            row = c.execute("select name, vial_size_mg, medium, reconstituted_count, is_active from inventory_items where id = ?", (owners[line],)).fetchone()
            assert row == ("B-12", 5.0, "Drops", 0, 0)
        assert c.execute("select reconstituted_count, is_active from inventory_items where id = ?", (b12,)).fetchone() == (2, 1)
        assert c.execute("select count(*) from inventory_items where name = 'Solo'").fetchone()[0] == 1
        assert c.execute("select count(*) from inventory_items where name = 'Pads'").fetchone()[0] == 1
