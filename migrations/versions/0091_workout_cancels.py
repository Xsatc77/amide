"""workouts: cancel one session, counted as missed

Revision ID: 0091
Revises: 0090
Create Date: 2026-11-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0091'
down_revision: Union[str, None] = '0090'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workout_cancels',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_day_id', sa.Integer(), sa.ForeignKey('workout_plan_days.id', ondelete='CASCADE'), nullable=False),
        sa.Column('on_date', sa.Date(), nullable=False),
        sa.UniqueConstraint('plan_day_id', 'on_date', name='uq_workout_cancel_day_date'))
    op.create_index('ix_workout_cancels_owner_id', 'workout_cancels', ['owner_id'])
    op.create_index('ix_workout_cancels_plan_day_id', 'workout_cancels', ['plan_day_id'])
    op.create_index('ix_workout_cancels_on_date', 'workout_cancels', ['on_date'])


def downgrade() -> None:
    op.drop_table('workout_cancels')
