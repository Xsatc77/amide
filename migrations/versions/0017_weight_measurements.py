"""weight & measurements: User profile fields for the macro calculator, BodyMeasurement table

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0017'
down_revision: Union[str, None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sex', sa.Enum('Male', 'Female', name='biological_sex', native_enum=False, length=20), nullable=True))
        batch_op.add_column(sa.Column('birth_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('height_in', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('activity_level', sa.Enum('1.2', '1.375', '1.55', '1.725', '1.9', name='activity_level', native_enum=False, length=20), nullable=True))
        batch_op.add_column(sa.Column('macro_goal', sa.Enum('-1000', '-500', '-250', '0', '250', '500', '1000', name='macro_goal', native_enum=False, length=20), nullable=True))
        batch_op.add_column(sa.Column('diet_preset', sa.Enum('balanced', 'high_protein', 'low_carb', 'keto', 'custom', name='diet_preset', native_enum=False, length=20), nullable=True))
        batch_op.add_column(sa.Column('custom_protein_pct', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('custom_carb_pct', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('custom_fat_pct', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('water_goal_oz', sa.Integer(), nullable=True))

    op.create_table(
        'body_measurements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('measured_at', sa.Date(), nullable=False),
        sa.Column('weight_lbs', sa.Float(), nullable=True),
        sa.Column('systolic', sa.Integer(), nullable=True),
        sa.Column('diastolic', sa.Integer(), nullable=True),
        sa.Column('neck_in', sa.Float(), nullable=True),
        sa.Column('waist_in', sa.Float(), nullable=True),
        sa.Column('hips_in', sa.Float(), nullable=True),
        sa.Column('biceps_l_in', sa.Float(), nullable=True),
        sa.Column('biceps_r_in', sa.Float(), nullable=True),
        sa.Column('forearm_l_in', sa.Float(), nullable=True),
        sa.Column('forearm_r_in', sa.Float(), nullable=True),
        sa.Column('quad_l_in', sa.Float(), nullable=True),
        sa.Column('quad_r_in', sa.Float(), nullable=True),
        sa.Column('calf_l_in', sa.Float(), nullable=True),
        sa.Column('calf_r_in', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('weight_lbs IS NULL OR weight_lbs > 0', name='ck_body_measurement_weight_pos'),
        sa.CheckConstraint('systolic IS NULL OR systolic > 0', name='ck_body_measurement_systolic_pos'),
        sa.CheckConstraint('diastolic IS NULL OR diastolic > 0', name='ck_body_measurement_diastolic_pos'),
        sa.CheckConstraint('neck_in IS NULL OR neck_in > 0', name='ck_body_measurement_neck_pos'),
        sa.CheckConstraint('waist_in IS NULL OR waist_in > 0', name='ck_body_measurement_waist_pos'),
        sa.CheckConstraint('hips_in IS NULL OR hips_in > 0', name='ck_body_measurement_hips_pos'),
        sa.CheckConstraint('biceps_l_in IS NULL OR biceps_l_in > 0', name='ck_body_measurement_biceps_l_pos'),
        sa.CheckConstraint('biceps_r_in IS NULL OR biceps_r_in > 0', name='ck_body_measurement_biceps_r_pos'),
        sa.CheckConstraint('forearm_l_in IS NULL OR forearm_l_in > 0', name='ck_body_measurement_forearm_l_pos'),
        sa.CheckConstraint('forearm_r_in IS NULL OR forearm_r_in > 0', name='ck_body_measurement_forearm_r_pos'),
        sa.CheckConstraint('quad_l_in IS NULL OR quad_l_in > 0', name='ck_body_measurement_quad_l_pos'),
        sa.CheckConstraint('quad_r_in IS NULL OR quad_r_in > 0', name='ck_body_measurement_quad_r_pos'),
        sa.CheckConstraint('calf_l_in IS NULL OR calf_l_in > 0', name='ck_body_measurement_calf_l_pos'),
        sa.CheckConstraint('calf_r_in IS NULL OR calf_r_in > 0', name='ck_body_measurement_calf_r_pos'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_body_measurements_owner_id', 'body_measurements', ['owner_id'])
    op.create_index('ix_body_measurements_measured_at', 'body_measurements', ['measured_at'])


def downgrade() -> None:
    op.drop_index('ix_body_measurements_measured_at', table_name='body_measurements')
    op.drop_index('ix_body_measurements_owner_id', table_name='body_measurements')
    op.drop_table('body_measurements')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('water_goal_oz')
        batch_op.drop_column('custom_fat_pct')
        batch_op.drop_column('custom_carb_pct')
        batch_op.drop_column('custom_protein_pct')
        batch_op.drop_column('diet_preset')
        batch_op.drop_column('macro_goal')
        batch_op.drop_column('activity_level')
        batch_op.drop_column('height_in')
        batch_op.drop_column('birth_date')
        batch_op.drop_column('sex')
