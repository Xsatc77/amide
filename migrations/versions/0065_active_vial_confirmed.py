"""inventory: whether the amount left in an open vial has been confirmed, and when the person was last asked

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0065'
down_revision: Union[str, None] = '0064'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('active_vials', sa.Column('confirmed', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('active_vials', sa.Column('prompted_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('active_vials') as batch:
        batch.drop_column('prompted_at')
        batch.drop_column('confirmed')
