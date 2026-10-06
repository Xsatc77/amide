"""price_alert_ignores: products the administrator marked as not a peptide

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0032'
down_revision: Union[str, None] = '0031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'price_alert_ignores',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('product_key', sa.String(300), nullable=False),
        sa.Column('product_name', sa.String(300), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('product_key', name='uq_price_alert_ignore_key'),
    )


def downgrade() -> None:
    op.drop_table('price_alert_ignores')
