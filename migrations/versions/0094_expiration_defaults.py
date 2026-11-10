"""users: their own default expiration windows for opened BAC Water and lyophilized stock

Revision ID: 0094
Revises: 0093
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0094'
down_revision: Union[str, None] = '0093'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('bac_bottle_days', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('lyophilized_shelf_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('lyophilized_shelf_days')
        batch.drop_column('bac_bottle_days')
