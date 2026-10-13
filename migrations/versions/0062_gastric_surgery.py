"""settings: prior gastric surgery flag and an optional measured daily calorie need

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0062'
down_revision: Union[str, None] = '0061'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('prior_gastric_surgery', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('measured_tdee', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('measured_tdee')
        batch.drop_column('prior_gastric_surgery')
