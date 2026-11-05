"""order lines: how many arrived damaged at check-in

Revision ID: 0087
Revises: 0086
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0087'
down_revision: Union[str, None] = '0086'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('order_items') as batch:
        batch.add_column(sa.Column('damaged_quantity', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('order_items') as batch:
        batch.drop_column('damaged_quantity')
