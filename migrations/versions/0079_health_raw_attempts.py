"""apple health: how many times each raw export has been read, so one that always fails is given up on

Revision ID: 0079
Revises: 0078
Create Date: 2026-10-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0079'
down_revision: Union[str, None] = '0078'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('health_raw') as b:
        b.add_column(sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('health_raw') as b:
        b.drop_column('attempts')
