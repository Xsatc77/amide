"""water: each log keeps the goal it was logged against

Revision ID: 0070
Revises: 0069
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0070'
down_revision: Union[str, None] = '0069'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('water_logs', sa.Column('goal_oz', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('water_logs') as batch:
        batch.drop_column('goal_oz')
