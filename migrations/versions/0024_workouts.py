"""workouts: WorkoutPlan/Day/Exercise, WorkoutLog/ExerciseLog, FitnessTestResult

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0024'
down_revision: Union[str, None] = '0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workout_plans',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('source', sa.Enum('pdf', 'manual', name='workoutsource', native_enum=False, length=20),
                  nullable=False),
        sa.Column('source_pdf_filename', sa.String(100)),
        sa.Column('started_on', sa.Date(), nullable=False),
        sa.Column('ended_on', sa.Date()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_workout_plans_owner_id', 'workout_plans', ['owner_id'])

    op.create_table(
        'workout_plan_days',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('plan_id', sa.Integer(), sa.ForeignKey('workout_plans.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(200), nullable=False),
        sa.Column('weekdays', sa.String(7)),
    )
    op.create_index('ix_workout_plan_days_plan_id', 'workout_plan_days', ['plan_id'])

    op.create_table(
        'workout_exercises',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('day_id', sa.Integer(), sa.ForeignKey('workout_plan_days.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('sets_text', sa.String(50)),
        sa.Column('reps_text', sa.String(50)),
        sa.Column('rest_text', sa.String(50)),
    )
    op.create_index('ix_workout_exercises_day_id', 'workout_exercises', ['day_id'])

    op.create_table(
        'workout_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_day_id', sa.Integer(), sa.ForeignKey('workout_plan_days.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('log_date', sa.Date(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('plan_day_id', 'log_date', name='uq_workout_log_day_date'),
    )
    op.create_index('ix_workout_logs_owner_id', 'workout_logs', ['owner_id'])
    op.create_index('ix_workout_logs_log_date', 'workout_logs', ['log_date'])

    op.create_table(
        'workout_exercise_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('workout_log_id', sa.Integer(), sa.ForeignKey('workout_logs.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('exercise_id', sa.Integer(), sa.ForeignKey('workout_exercises.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('completed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('weight_value', sa.Float()),
        sa.Column('weight_unit', sa.Enum('lb', 'kg', name='weightunit', native_enum=False, length=20)),
        sa.Column('reps_value', sa.Integer()),
    )
    op.create_index('ix_workout_exercise_logs_workout_log_id', 'workout_exercise_logs', ['workout_log_id'])

    op.create_table(
        'fitness_test_results',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('exercise', sa.Enum(
            'max_pushups', 'max_situps', 'max_bodyweight_squats', 'plank_hold_seconds',
            name='fitnesstestexercisename', native_enum=False, length=25), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('tested_at', sa.Date(), nullable=False),
    )
    op.create_index('ix_fitness_test_results_owner_id', 'fitness_test_results', ['owner_id'])
    op.create_index('ix_fitness_test_results_tested_at', 'fitness_test_results', ['tested_at'])


def downgrade() -> None:
    op.drop_table('fitness_test_results')
    op.drop_table('workout_exercise_logs')
    op.drop_table('workout_logs')
    op.drop_table('workout_exercises')
    op.drop_table('workout_plan_days')
    op.drop_table('workout_plans')
