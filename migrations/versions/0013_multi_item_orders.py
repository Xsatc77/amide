"""multi-item orders: split Order into an order header + OrderItem lines

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        'order_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('inventory_item_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('received_quantity', sa.Integer(), nullable=True),
        sa.Column('received_note', sa.String(length=300), nullable=True),
        sa.Column('cost_cents', sa.Integer(), nullable=True),
        sa.Column('lot_number', sa.String(length=100), nullable=True),
        sa.Column('expiration_date', sa.Date(), nullable=True),
        sa.Column('coa_filename', sa.String(length=100), nullable=True),
        sa.Column('coa_vial_size_mg', sa.Float(), nullable=True),
        sa.Column('coa_purity_pct', sa.Float(), nullable=True),
        sa.CheckConstraint('quantity > 0', name='ck_order_item_quantity_pos'),
        sa.CheckConstraint(
            'received_quantity IS NULL OR (received_quantity >= 0 AND received_quantity <= quantity)',
            name='ck_order_item_received_range'),
        sa.CheckConstraint('cost_cents IS NULL OR cost_cents >= 0', name='ck_order_item_cost_nonneg'),
        sa.CheckConstraint('coa_vial_size_mg IS NULL OR coa_vial_size_mg > 0', name='ck_order_item_coa_vial_size_pos'),
        sa.CheckConstraint('coa_purity_pct IS NULL OR (coa_purity_pct >= 0 AND coa_purity_pct <= 100)',
                           name='ck_order_item_coa_purity_range'),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_order_items_order_id', 'order_items', ['order_id'])
    op.create_index('ix_order_items_inventory_item_id', 'order_items', ['inventory_item_id'])

    # Backfill: one OrderItem per existing Order row. received_quantity backfills to quantity
    # wherever arrival_date is already set (that stock already counted as available under the old
    # model) and stays NULL otherwise (still in transit, nothing to check in retroactively).
    rows = bind.execute(text("""
        SELECT id, inventory_item_id, quantity, cost_cents, lot_number, expiration_date,
               coa_filename, coa_vial_size_mg, coa_purity_pct, arrival_date
        FROM orders
    """)).fetchall()
    for r in rows:
        bind.execute(text("""
            INSERT INTO order_items (order_id, inventory_item_id, quantity, received_quantity,
                                     cost_cents, lot_number, expiration_date, coa_filename,
                                     coa_vial_size_mg, coa_purity_pct)
            VALUES (:order_id, :item_id, :qty, :received_qty, :cost_cents, :lot_number,
                    :expiration_date, :coa_filename, :coa_vial_size_mg, :coa_purity_pct)
        """), {"order_id": r.id, "item_id": r.inventory_item_id, "qty": r.quantity,
              "received_qty": r.quantity if r.arrival_date is not None else None,
              "cost_cents": r.cost_cents, "lot_number": r.lot_number,
              "expiration_date": r.expiration_date, "coa_filename": r.coa_filename,
              "coa_vial_size_mg": r.coa_vial_size_mg, "coa_purity_pct": r.coa_purity_pct})

    # Drop the index before the batch recreate below -- SQLite batch mode reflects existing
    # indexes and would otherwise try to recreate this one on inventory_item_id after that
    # column is dropped in the same batch, which fails.
    op.drop_index('ix_orders_inventory_item_id', table_name='orders')
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_constraint('ck_order_quantity_pos', type_='check')
        batch_op.drop_constraint('ck_order_cost_nonneg', type_='check')
        batch_op.drop_constraint('ck_order_coa_vial_size_pos', type_='check')
        batch_op.drop_constraint('ck_order_coa_purity_range', type_='check')
        batch_op.drop_column('inventory_item_id')
        batch_op.drop_column('quantity')
        batch_op.drop_column('cost_cents')
        batch_op.drop_column('lot_number')
        batch_op.drop_column('expiration_date')
        batch_op.drop_column('coa_filename')
        batch_op.drop_column('coa_vial_size_mg')
        batch_op.drop_column('coa_purity_pct')


def downgrade() -> None:
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('coa_purity_pct', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('coa_vial_size_mg', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('coa_filename', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('expiration_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('lot_number', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('cost_cents', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('quantity', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('inventory_item_id', sa.Integer(), nullable=True))
        batch_op.create_check_constraint('ck_order_coa_purity_range',
                                         'coa_purity_pct IS NULL OR (coa_purity_pct >= 0 AND coa_purity_pct <= 100)')
        batch_op.create_check_constraint('ck_order_coa_vial_size_pos', 'coa_vial_size_mg IS NULL OR coa_vial_size_mg > 0')
        batch_op.create_check_constraint('ck_order_cost_nonneg', 'cost_cents IS NULL OR cost_cents >= 0')
        batch_op.create_check_constraint('ck_order_quantity_pos', 'quantity > 0')
    op.create_index('ix_orders_inventory_item_id', 'orders', ['inventory_item_id'])

    # Best-effort: only correct if every order still has exactly one line (true immediately after
    # this migration's upgrade with no multi-item orders committed since -- downgrading after real
    # multi-item orders exist collapses each order down to just its first line).
    bind = op.get_bind()
    rows = bind.execute(text("""
        SELECT order_id, inventory_item_id, quantity, cost_cents, lot_number, expiration_date,
               coa_filename, coa_vial_size_mg, coa_purity_pct
        FROM order_items
        WHERE id IN (SELECT MIN(id) FROM order_items GROUP BY order_id)
    """)).fetchall()
    for r in rows:
        bind.execute(text("""
            UPDATE orders SET inventory_item_id=:item_id, quantity=:qty, cost_cents=:cost_cents,
                              lot_number=:lot_number, expiration_date=:expiration_date,
                              coa_filename=:coa_filename, coa_vial_size_mg=:coa_vial_size_mg,
                              coa_purity_pct=:coa_purity_pct
            WHERE id=:order_id
        """), {"order_id": r.order_id, "item_id": r.inventory_item_id, "qty": r.quantity,
              "cost_cents": r.cost_cents, "lot_number": r.lot_number,
              "expiration_date": r.expiration_date, "coa_filename": r.coa_filename,
              "coa_vial_size_mg": r.coa_vial_size_mg, "coa_purity_pct": r.coa_purity_pct})
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.alter_column('inventory_item_id', nullable=False)
        batch_op.alter_column('quantity', nullable=False)
    op.drop_index('ix_order_items_inventory_item_id', table_name='order_items')
    op.drop_index('ix_order_items_order_id', table_name='order_items')
    op.drop_table('order_items')
