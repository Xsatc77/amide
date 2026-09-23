"""peptide card details

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = [
    sa.Column('card_class', sa.String(length=200), nullable=True),
    sa.Column('category', sa.String(length=200), nullable=True),
    sa.Column('evidence_level', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=200), nullable=True),
    sa.Column('card_details', sa.JSON(), nullable=True),
    sa.Column('card_image', sa.String(length=100), nullable=True),
]


def upgrade() -> None:
    with op.batch_alter_table('peptides', schema=None) as batch_op:
        for column in COLUMNS:
            batch_op.add_column(column.copy())


def downgrade() -> None:
    with op.batch_alter_table('peptides', schema=None) as batch_op:
        for column in reversed(COLUMNS):
            batch_op.drop_column(column.name)
