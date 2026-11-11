"""workout_drafts: one exercise's sets and reps committed part-way through a workout, before the whole log is saved

Revision ID: 0096
Revises: 0095
Create Date: 2026-11-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0096'
down_revision: Union[str, None] = '0095'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workout_drafts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_day_id', sa.Integer(), sa.ForeignKey('workout_plan_days.id', ondelete='CASCADE'), nullable=False),
        sa.Column('exercise_id', sa.Integer(), sa.ForeignKey('workout_exercises.id', ondelete='CASCADE'), nullable=False),
        sa.Column('log_date', sa.Date(), nullable=False),
        sa.Column('fields', sa.Text(), nullable=False),
        sa.Column('saved_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('exercise_id', 'log_date', name='uq_workout_draft_exercise_date'),
    )
    op.create_index('ix_workout_drafts_owner_id', 'workout_drafts', ['owner_id'])
    op.create_index('ix_workout_drafts_plan_day_id', 'workout_drafts', ['plan_day_id'])


def downgrade() -> None:
    op.drop_index('ix_workout_drafts_plan_day_id', table_name='workout_drafts')
    op.drop_index('ix_workout_drafts_owner_id', table_name='workout_drafts')
    op.drop_table('workout_drafts')
