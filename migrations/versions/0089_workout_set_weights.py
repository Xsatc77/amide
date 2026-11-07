"""workout logs: a weight and reps for each set, and a to-failure mark

Revision ID: 0089
Revises: 0088
Create Date: 2026-11-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0089'
down_revision: Union[str, None] = '0088'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('workout_exercise_logs') as batch:
        batch.add_column(sa.Column('set_weights', sa.String(length=600), nullable=True))
        batch.add_column(sa.Column('set_reps', sa.String(length=600), nullable=True))
        batch.add_column(sa.Column('to_failure', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('workout_exercise_logs') as batch:
        batch.drop_column('to_failure')
        batch.drop_column('set_reps')
        batch.drop_column('set_weights')
