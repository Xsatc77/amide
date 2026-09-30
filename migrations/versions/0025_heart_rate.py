"""body_measurements: heart_rate_bpm (taken alongside blood pressure at the same weigh-in)

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0025'
down_revision: Union[str, None] = '0024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('body_measurements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('heart_rate_bpm', sa.Integer(), nullable=True))
        batch_op.create_check_constraint('ck_body_measurement_heart_rate_pos', 'heart_rate_bpm IS NULL OR heart_rate_bpm > 0')


def downgrade() -> None:
    with op.batch_alter_table('body_measurements', schema=None) as batch_op:
        batch_op.drop_constraint('ck_body_measurement_heart_rate_pos', type_='check')
        batch_op.drop_column('heart_rate_bpm')
