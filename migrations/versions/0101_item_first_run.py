"""protocol_items.first_run: the run of a short course an item first joins in

Revision ID: 0101
Revises: 0100
Create Date: 2026-11-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0101'
down_revision: Union[str, None] = '0100'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('protocol_items') as batch:
        batch.add_column(sa.Column('first_run', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    with op.batch_alter_table('protocol_items') as batch:
        batch.drop_column('first_run')
