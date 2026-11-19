"""element_symbols: each peptide's own periodic-table symbol, never shared by two

Revision ID: 0103
Revises: 0102
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0103'
down_revision: Union[str, None] = '0102'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'element_symbols',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=200, collation='NOCASE'), nullable=False, unique=True),
        sa.Column('symbol', sa.String(length=4, collation='NOCASE'), nullable=False, unique=True),
    )


def downgrade() -> None:
    op.drop_table('element_symbols')
