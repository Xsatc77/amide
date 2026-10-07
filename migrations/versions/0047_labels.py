"""users: print vial labels automatically on check-in, and the label size

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0047'
down_revision: Union[str, None] = '0046'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('auto_print_labels', sa.Boolean(), nullable=False, server_default='1'))
    op.add_column('users', sa.Column('label_size', sa.String(10), nullable=False, server_default='5160'))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('label_size')
        batch.drop_column('auto_print_labels')
