"""workout history that survives plan changes, with the data behind calorie estimates

A logged workout used to be deleted when its plan day or exercise was removed (ON DELETE CASCADE). History is now
kept: the log keeps a snapshot of its day label and plan name, every exercise log keeps its own name and what its
calorie estimate used, and the foreign keys to the plan rows become ON DELETE SET NULL. Plan exercises also gain
the database exercise they were matched to.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0034'
down_revision: Union[str, None] = '0033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 0024 created these foreign keys without names; this convention gives the reflected ones a name to drop by.
_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}

_LOG_COLUMNS = (
    ('name', sa.String(200)), ('db_exercise', sa.String(200)), ('area', sa.String(60)), ('equipment', sa.String(60)),
    ('sets', sa.Integer()), ('duration_min', sa.Float()), ('speed_mph', sa.Float()), ('grade_pct', sa.Float()),
    ('implements', sa.Integer()), ('style', sa.String(40)), ('sec_per_rep', sa.Float()), ('rest_min', sa.Float()),
    ('met', sa.Float()), ('body_weight_lb', sa.Float()), ('gross_kcal', sa.Float()), ('net_kcal', sa.Float()),
    ('volume_lb', sa.Float()), ('compendium_code', sa.String(10)), ('kcal_note', sa.String(200)),
)


def upgrade() -> None:
    with op.batch_alter_table('workout_exercises') as batch_op:
        batch_op.add_column(sa.Column('db_exercise', sa.String(200)))
        batch_op.add_column(sa.Column('db_exercise_confirmed', sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table('workout_logs', naming_convention=_NAMING) as batch_op:
        batch_op.add_column(sa.Column('day_label', sa.String(200)))
        batch_op.add_column(sa.Column('plan_name', sa.String(200)))
        batch_op.alter_column('plan_day_id', existing_type=sa.Integer(), nullable=True)
        batch_op.drop_constraint('fk_workout_logs_plan_day_id_workout_plan_days', type_='foreignkey')
        batch_op.create_foreign_key('fk_workout_logs_plan_day_id_workout_plan_days', 'workout_plan_days',
                                    ['plan_day_id'], ['id'], ondelete='SET NULL')
    op.execute(
        "UPDATE workout_logs SET "
        "day_label = (SELECT d.label FROM workout_plan_days d WHERE d.id = workout_logs.plan_day_id), "
        "plan_name = (SELECT p.name FROM workout_plan_days d JOIN workout_plans p ON p.id = d.plan_id "
        "WHERE d.id = workout_logs.plan_day_id)")

    with op.batch_alter_table('workout_exercise_logs', naming_convention=_NAMING) as batch_op:
        for name, type_ in _LOG_COLUMNS:
            batch_op.add_column(sa.Column(name, type_))
        batch_op.alter_column('exercise_id', existing_type=sa.Integer(), nullable=True)
        batch_op.drop_constraint('fk_workout_exercise_logs_exercise_id_workout_exercises', type_='foreignkey')
        batch_op.create_foreign_key('fk_workout_exercise_logs_exercise_id_workout_exercises', 'workout_exercises',
                                    ['exercise_id'], ['id'], ondelete='SET NULL')
    op.execute(
        "UPDATE workout_exercise_logs SET "
        "name = (SELECT e.name FROM workout_exercises e WHERE e.id = workout_exercise_logs.exercise_id), "
        "implements = 1")


def downgrade() -> None:
    # The old schema cannot hold history that no longer has a plan row, so that history is dropped here.
    op.execute("DELETE FROM workout_exercise_logs WHERE exercise_id IS NULL")
    op.execute("DELETE FROM workout_logs WHERE plan_day_id IS NULL")
    with op.batch_alter_table('workout_exercise_logs', naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint('fk_workout_exercise_logs_exercise_id_workout_exercises', type_='foreignkey')
        batch_op.create_foreign_key('fk_workout_exercise_logs_exercise_id_workout_exercises', 'workout_exercises',
                                    ['exercise_id'], ['id'], ondelete='CASCADE')
        batch_op.alter_column('exercise_id', existing_type=sa.Integer(), nullable=False)
        for name, _ in reversed(_LOG_COLUMNS):
            batch_op.drop_column(name)
    with op.batch_alter_table('workout_logs', naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint('fk_workout_logs_plan_day_id_workout_plan_days', type_='foreignkey')
        batch_op.create_foreign_key('fk_workout_logs_plan_day_id_workout_plan_days', 'workout_plan_days',
                                    ['plan_day_id'], ['id'], ondelete='CASCADE')
        batch_op.alter_column('plan_day_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('plan_name')
        batch_op.drop_column('day_label')
    with op.batch_alter_table('workout_exercises') as batch_op:
        batch_op.drop_column('db_exercise_confirmed')
        batch_op.drop_column('db_exercise')
