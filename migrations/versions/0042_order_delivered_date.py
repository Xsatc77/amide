"""orders.delivered_date: when the package reached the door (check-in comes after)

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0042'
down_revision: Union[str, None] = '0041'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('delivered_date', sa.Date()))


def downgrade() -> None:
    with op.batch_alter_table('orders') as batch:
        batch.drop_column('delivered_date')
