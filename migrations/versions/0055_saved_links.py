"""links: each person's saved sites

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0055'
down_revision: Union[str, None] = '0054'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'saved_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('url', sa.String(500), nullable=False),
        sa.Column('link_type', sa.String(20), nullable=False),
        sa.Column('description', sa.String(300), nullable=True),
        sa.Column('auto_description', sa.String(300), nullable=True),
        sa.Column('auto_checked', sa.Boolean(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_table('saved_links')
