"""inventory: doses use up non-vial items a unit at a time

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0066'
down_revision: Union[str, None] = '0065'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('inventory_items', sa.Column('dosed_units', sa.Float(), nullable=False, server_default='0'))
    op.add_column('dose_logs', sa.Column('inventory_units', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('dose_logs') as batch:
        batch.drop_column('inventory_units')
    with op.batch_alter_table('inventory_items') as batch:
        batch.drop_column('dosed_units')
