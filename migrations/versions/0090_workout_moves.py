"""workouts: move one session to another day, for that session only

Revision ID: 0090
Revises: 0089
Create Date: 2026-11-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0090'
down_revision: Union[str, None] = '0089'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workout_moves',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_day_id', sa.Integer(), sa.ForeignKey('workout_plan_days.id', ondelete='CASCADE'), nullable=False),
        sa.Column('from_date', sa.Date(), nullable=False),
        sa.Column('to_date', sa.Date(), nullable=False),
        sa.UniqueConstraint('plan_day_id', 'from_date', name='uq_workout_move_day_from'))
    op.create_index('ix_workout_moves_owner_id', 'workout_moves', ['owner_id'])
    op.create_index('ix_workout_moves_plan_day_id', 'workout_moves', ['plan_day_id'])
    op.create_index('ix_workout_moves_to_date', 'workout_moves', ['to_date'])


def downgrade() -> None:
    op.drop_table('workout_moves')
