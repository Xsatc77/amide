"""settings: display units (US or metric)

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0054'
down_revision: Union[str, None] = '0053'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('units', sa.String(6), nullable=False, server_default='us'))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('units')
