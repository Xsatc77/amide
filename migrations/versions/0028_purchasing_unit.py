"""inventory_items.purchasing_unit: individual vial vs. a 10-vial kit, for as-needed course totals

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0028'
down_revision: Union[str, None] = '0027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'purchasing_unit', sa.String(length=20), nullable=False, server_default='individual'))


def downgrade() -> None:
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.drop_column('purchasing_unit')
