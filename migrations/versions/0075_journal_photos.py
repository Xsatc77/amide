"""journal_photos: pictures kept with a journal day

Revision ID: 0075
Revises: 0074
Create Date: 2026-10-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0075'
down_revision: Union[str, None] = '0074'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'journal_photos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('entry_id', sa.Integer(), sa.ForeignKey('journal_entries.id', ondelete='CASCADE'), nullable=False),
        sa.Column('filename', sa.String(64), nullable=False),
        sa.Column('caption', sa.String(200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_journal_photos_entry_id', 'journal_photos', ['entry_id'])


def downgrade() -> None:
    op.drop_index('ix_journal_photos_entry_id', table_name='journal_photos')
    op.drop_table('journal_photos')
