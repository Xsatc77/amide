"""price_corrections: what the administrator says a vendor's product name is

Revision ID: 0073
Revises: 0072
Create Date: 2026-10-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0073'
down_revision: Union[str, None] = '0072'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'price_corrections',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('product_key', sa.String(300), nullable=False),
        sa.Column('product_name', sa.String(300), nullable=False),
        sa.Column('peptide_id', sa.Integer(), sa.ForeignKey('peptides.id', ondelete='CASCADE'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('product_key', name='uq_price_correction_key'),
    )


def downgrade() -> None:
    op.drop_table('price_corrections')
