"""peptides.library_specifications: comma-separated list of available specs from price sheets

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0030'
down_revision: Union[str, None] = '0029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('peptides', schema=None) as batch_op:
        batch_op.add_column(sa.Column('library_specifications', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('peptides', schema=None) as batch_op:
        batch_op.drop_column('library_specifications')
