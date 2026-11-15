"""dose_labs_skip: a skipped dose can be marked an intentional skip for labs, which compliance does not count as missed

Revision ID: 0097
Revises: 0096
Create Date: 2026-11-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0097'
down_revision: Union[str, None] = '0096'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('dose_logs') as batch:
        batch.add_column(sa.Column('labs_skip', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('dose_logs') as batch:
        batch.drop_column('labs_skip')
