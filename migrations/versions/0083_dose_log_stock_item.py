"""dose logs: remember which inventory item a dose took its units from

Revision ID: 0083
Revises: 0082
Create Date: 2026-10-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0083'
down_revision: Union[str, None] = '0082'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('dose_logs') as batch:
        batch.add_column(sa.Column('stock_item_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_dose_logs_stock_item_id_inventory_items', 'inventory_items', ['stock_item_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('dose_logs') as batch:
        batch.drop_column('stock_item_id')
