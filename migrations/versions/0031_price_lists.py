"""price_lists and price_list_items: imported vendor price lists (pack prices, warehouse region)

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0031'
down_revision: Union[str, None] = '0030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'price_lists',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vendor_name', sa.String(200), nullable=False),
        sa.Column('vendor_id', sa.Integer(), sa.ForeignKey('vendors.id', ondelete='SET NULL')),
        sa.Column('warehouse', sa.Enum('us', 'china', name='warehouse', native_enum=False, length=20),
                  nullable=False),
        sa.Column('warehouse_source',
                  sa.Enum('filename', 'text', 'assumed', 'manual', name='warehousesource', native_enum=False,
                          length=20),
                  nullable=False),
        sa.Column('list_date', sa.Date(), nullable=False),
        sa.Column('source_filename', sa.String(300), nullable=False),
        sa.Column('shipping_note', sa.Text()),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('source_filename', name='uq_price_list_source_filename'),
    )
    op.create_index('ix_price_lists_vendor_id', 'price_lists', ['vendor_id'])

    op.create_table(
        'price_list_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('price_list_id', sa.Integer(), sa.ForeignKey('price_lists.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('code', sa.String(30)),
        sa.Column('product_name', sa.String(300)),
        sa.Column('peptide_id', sa.Integer(), sa.ForeignKey('peptides.id', ondelete='SET NULL')),
        sa.Column('vial_amount', sa.Float(), nullable=False),
        sa.Column('vial_unit', sa.String(10), nullable=False),
        sa.Column('pack_size', sa.Integer()),
        sa.Column('pack_price', sa.Float()),
        sa.Column('pack_type', sa.String(10)),
        sa.Column('extra_prices', sa.JSON()),
        sa.Column('flags', sa.JSON()),
    )
    op.create_index('ix_price_list_items_price_list_id', 'price_list_items', ['price_list_id'])
    op.create_index('ix_price_list_items_peptide_id', 'price_list_items', ['peptide_id'])


def downgrade() -> None:
    op.drop_table('price_list_items')
    op.drop_table('price_lists')
