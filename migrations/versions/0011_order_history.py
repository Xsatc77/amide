"""order history: orders table, item categories

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CATEGORY_VALUES = ('Medicine', 'BAC Water', 'Supply')


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        'orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('inventory_item_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('order_date', sa.Date(), nullable=False),
        sa.Column('shipped_date', sa.Date(), nullable=True),
        sa.Column('arrival_date', sa.Date(), nullable=True),
        sa.Column('tracking_site', sa.String(length=500), nullable=True),
        sa.Column('tracking_number', sa.String(length=100), nullable=True),
        sa.Column('vendor', sa.String(length=200), nullable=True),
        sa.Column('vendor_id', sa.Integer(), nullable=True),
        sa.Column('lot_number', sa.String(length=100), nullable=True),
        sa.Column('cost_cents', sa.Integer(), nullable=True),
        sa.Column('tax_cents', sa.Integer(), nullable=True),
        sa.Column('shipping_cents', sa.Integer(), nullable=True),
        sa.Column('expiration_date', sa.Date(), nullable=True),
        sa.Column('coa_filename', sa.String(length=100), nullable=True),
        sa.Column('coa_vial_size_mg', sa.Float(), nullable=True),
        sa.Column('coa_purity_pct', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('quantity > 0', name='ck_order_quantity_pos'),
        sa.CheckConstraint('cost_cents IS NULL OR cost_cents >= 0', name='ck_order_cost_nonneg'),
        sa.CheckConstraint('tax_cents IS NULL OR tax_cents >= 0', name='ck_order_tax_nonneg'),
        sa.CheckConstraint('shipping_cents IS NULL OR shipping_cents >= 0', name='ck_order_shipping_nonneg'),
        sa.CheckConstraint('coa_vial_size_mg IS NULL OR coa_vial_size_mg > 0', name='ck_order_coa_vial_size_pos'),
        sa.CheckConstraint('coa_purity_pct IS NULL OR (coa_purity_pct >= 0 AND coa_purity_pct <= 100)',
                           name='ck_order_coa_purity_range'),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_orders_inventory_item_id', 'orders', ['inventory_item_id'])

    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'category', sa.Enum(*CATEGORY_VALUES, name='category', native_enum=False, length=20),
            nullable=False, server_default='Medicine'))
        batch_op.add_column(sa.Column('reconstituted_count', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('sold_count', sa.Integer(), nullable=False, server_default='0'))

    # Backfill: medium set -> Medicine (already the column default), no medium -> Supply (matches
    # today's real usage -- alcohol pads/syringes already have no medium). Nothing existing maps
    # to BAC Water automatically; recategorize by deleting and re-adding if needed.
    bind.execute(text("UPDATE inventory_items SET category = 'Supply' WHERE medium IS NULL"))

    # For every Medicine item with any order-shaped data or existing stock, synthesize one
    # already-arrived Order carrying the old per-item fields across, so existing stock isn't
    # stranded off of available_count after upgrading.
    rows = bind.execute(text("""
        SELECT id, count, vendor, vendor_id, lot_number, cost_cents, expiration_date,
               order_date, shipped_date, arrival_date, coa_filename, coa_vial_size_mg,
               coa_purity_pct, created_at
        FROM inventory_items WHERE category = 'Medicine'
    """)).fetchall()
    for r in rows:
        has_order_data = any([r.vendor, r.vendor_id, r.lot_number, r.cost_cents, r.expiration_date,
                              r.order_date, r.shipped_date, r.arrival_date, r.coa_filename,
                              r.coa_vial_size_mg, r.coa_purity_pct])
        if not has_order_data and not r.count:
            continue
        order_date = r.order_date or (r.created_at[:10] if r.created_at else None)
        arrival_date = r.arrival_date or order_date
        # quantity>0 is a hard constraint, so a fully-consumed item (count==0) still gets a
        # qty=1 synthetic Order -- but its unit is immediately marked reconstituted below, so
        # the net available_count stays 0, matching the pre-migration state.
        bind.execute(text("""
            INSERT INTO orders (inventory_item_id, quantity, order_date, shipped_date, arrival_date,
                                vendor, vendor_id, lot_number, cost_cents, expiration_date,
                                coa_filename, coa_vial_size_mg, coa_purity_pct, created_at)
            VALUES (:item_id, :qty, :order_date, :shipped_date, :arrival_date, :vendor, :vendor_id,
                    :lot_number, :cost_cents, :expiration_date, :coa_filename, :coa_vial_size_mg,
                    :coa_purity_pct, :created_at)
        """), {"item_id": r.id, "qty": max(r.count, 1), "order_date": order_date,
              "shipped_date": r.shipped_date, "arrival_date": arrival_date, "vendor": r.vendor,
              "vendor_id": r.vendor_id, "lot_number": r.lot_number, "cost_cents": r.cost_cents,
              "expiration_date": r.expiration_date, "coa_filename": r.coa_filename,
              "coa_vial_size_mg": r.coa_vial_size_mg, "coa_purity_pct": r.coa_purity_pct,
              "created_at": r.created_at})
        if not r.count:
            bind.execute(text(
                "UPDATE inventory_items SET reconstituted_count = reconstituted_count + 1 WHERE id = :item_id"
            ), {"item_id": r.id})

    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.drop_constraint('ck_inventory_coa_vial_size_pos', type_='check')
        batch_op.drop_constraint('ck_inventory_coa_purity_range', type_='check')
        batch_op.drop_column('lot_number')
        batch_op.drop_column('order_date')
        batch_op.drop_column('shipped_date')
        batch_op.drop_column('arrival_date')
        batch_op.drop_column('coa_filename')
        batch_op.drop_column('coa_vial_size_mg')
        batch_op.drop_column('coa_purity_pct')


def downgrade() -> None:
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('coa_purity_pct', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('coa_vial_size_mg', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('coa_filename', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('arrival_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('shipped_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('order_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('lot_number', sa.String(length=100), nullable=True))
        batch_op.create_check_constraint('ck_inventory_coa_vial_size_pos',
                                         'coa_vial_size_mg IS NULL OR coa_vial_size_mg > 0')
        batch_op.create_check_constraint('ck_inventory_coa_purity_range',
                                         'coa_purity_pct IS NULL OR (coa_purity_pct >= 0 AND coa_purity_pct <= 100)')
        batch_op.drop_column('sold_count')
        batch_op.drop_column('reconstituted_count')
        batch_op.drop_column('category')
    op.drop_index('ix_orders_inventory_item_id', table_name='orders')
    op.drop_table('orders')
