"""inventory: remember how much of a supply order line has been added to the supply's count

Revision ID: 0068
Revises: 0067
Create Date: 2026-10-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0068'
down_revision: Union[str, None] = '0067'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('order_items', sa.Column('stock_applied', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('order_items') as batch:
        batch.drop_column('stock_applied')
