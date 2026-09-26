"""dashboard thresholds: InventoryItem.low_stock_threshold, User.low_stock_default/shipment_delay_days

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0015'
down_revision: Union[str, None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('low_stock_threshold', sa.Integer(), nullable=True))
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('low_stock_default', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('shipment_delay_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('shipment_delay_days')
        batch_op.drop_column('low_stock_default')
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.drop_column('low_stock_threshold')
