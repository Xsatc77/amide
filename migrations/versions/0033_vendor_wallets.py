"""vendor_wallets: crypto wallet addresses (with an optional QR photo) on a vendor

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0033'
down_revision: Union[str, None] = '0032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vendor_wallets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vendor_id', sa.Integer(), sa.ForeignKey('vendors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('coin', sa.String(8), nullable=False),
        sa.Column('address', sa.String(200), nullable=False),
        sa.Column('network', sa.String(60)),
        sa.Column('qr_filename', sa.String(120)),
    )
    op.create_index('ix_vendor_wallets_vendor_id', 'vendor_wallets', ['vendor_id'])


def downgrade() -> None:
    op.drop_index('ix_vendor_wallets_vendor_id', table_name='vendor_wallets')
    op.drop_table('vendor_wallets')
