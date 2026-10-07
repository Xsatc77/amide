"""journal: each person's own side-effect list, and which ones an entry ticked

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0052'
down_revision: Union[str, None] = '0051'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'journal_custom_effects',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('name', sa.String(40), nullable=False),
        sa.UniqueConstraint('owner_id', 'name', name='uq_journal_custom_effect'),
    )
    op.create_table(
        'journal_entry_custom_effects',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('entry_id', sa.Integer(), sa.ForeignKey('journal_entries.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('name', sa.String(40), nullable=False),
        sa.UniqueConstraint('entry_id', 'name', name='uq_journal_entry_custom_effect'),
    )


def downgrade() -> None:
    op.drop_table('journal_entry_custom_effects')
    op.drop_table('journal_custom_effects')
