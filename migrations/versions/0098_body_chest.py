"""body_chest: a chest tape measurement on each body entry

Revision ID: 0098
Revises: 0097
Create Date: 2026-11-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0098'
down_revision: Union[str, None] = '0097'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('body_measurements') as batch:
        batch.add_column(sa.Column('chest_in', sa.Float(), nullable=True))
        batch.create_check_constraint('ck_body_measurement_chest_pos', 'chest_in IS NULL OR chest_in > 0')


def downgrade() -> None:
    with op.batch_alter_table('body_measurements') as batch:
        batch.drop_constraint('ck_body_measurement_chest_pos', type_='check')
        batch.drop_column('chest_in')
