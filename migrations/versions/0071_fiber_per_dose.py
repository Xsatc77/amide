"""a fiber pill or powder adds its fiber to the day when a dose is taken

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0071'
down_revision: Union[str, None] = '0070'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('inventory_items', sa.Column('fiber_g_per_dose', sa.Float(), nullable=True))
    op.add_column('dose_logs', sa.Column('fiber_g', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('dose_logs') as batch:
        batch.drop_column('fiber_g')
    with op.batch_alter_table('inventory_items') as batch:
        batch.drop_column('fiber_g_per_dose')
