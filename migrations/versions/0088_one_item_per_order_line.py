"""inventory: every order line is its own item (two bottles and a kit of B-12 are three entries)

An item that holds more than one order line keeps its earliest line, with all its use so far (vials opened, sold,
doses taken), its Active state and the protocol lines pointing at it. Each further line moves to a new item copied
from it, with nothing used yet. Supplies count by item, not by line, so they are left as they are.

Revision ID: 0088
Revises: 0087
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0088'
down_revision: Union[str, None] = '0087'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RESET = {"count": 1, "reconstituted_count": 0, "sold_count": 0, "dosed_units": 0.0, "is_active": 0}


def upgrade() -> None:
    conn = op.get_bind()
    cols = [r[1] for r in conn.exec_driver_sql("pragma table_info(inventory_items)")]
    copy_cols = [c for c in cols if c != "id"]
    items = conn.exec_driver_sql(
        "select i.id from inventory_items i where i.category != 'Supply' "
        "and (select count(*) from order_items o where o.inventory_item_id = i.id) > 1").fetchall()
    for (item_id,) in items:
        lines = [r[0] for r in conn.exec_driver_sql(
            "select id from order_items where inventory_item_id = ? order by id", (item_id,))]
        row = dict(zip(cols, conn.exec_driver_sql("select * from inventory_items where id = ?", (item_id,)).fetchone()))
        for line_id in lines[1:]:
            values = {c: _RESET.get(c, row[c]) for c in copy_cols}
            conn.execute(
                sa.text(f"insert into inventory_items ({', '.join(copy_cols)}) values ({', '.join(':' + c for c in copy_cols)})"),
                values)
            new_id = conn.exec_driver_sql("select max(id) from inventory_items").fetchone()[0]
            conn.exec_driver_sql("update order_items set inventory_item_id = ? where id = ?", (new_id, line_id))


def downgrade() -> None:
    pass        # the split items stay split; there is nothing to put back
