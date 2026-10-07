"""users: the shipping fees the shopping plan assumes

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0045'
down_revision: Union[str, None] = '0044'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('shop_china_shipping_cents', sa.Integer()))
    op.add_column('users', sa.Column('shop_us_shipping_cents', sa.Integer()))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('shop_us_shipping_cents')
        batch.drop_column('shop_china_shipping_cents')
