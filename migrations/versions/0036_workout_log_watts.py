"""workout_exercise_logs.watts: the power entered for a bike or rowing row, so its Compendium row can be shown again

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0036'
down_revision: Union[str, None] = '0035'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('workout_exercise_logs') as batch_op:
        batch_op.add_column(sa.Column('watts', sa.Float()))


def downgrade() -> None:
    with op.batch_alter_table('workout_exercise_logs') as batch_op:
        batch_op.drop_column('watts')
