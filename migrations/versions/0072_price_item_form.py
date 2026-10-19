"""price lists: a line can be a dual chamber cartridge rather than a plain vial

Revision ID: 0072
Revises: 0071
Create Date: 2026-10-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0072'
down_revision: Union[str, None] = '0071'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('price_list_items', sa.Column('form', sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('price_list_items') as batch:
        batch.drop_column('form')
