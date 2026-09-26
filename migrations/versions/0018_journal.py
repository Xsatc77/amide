"""journal: JournalEntry, JournalEntrySideEffect, JournalQuickNote

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0018'
down_revision: Union[str, None] = '0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'journal_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('entry_date', sa.Date(), nullable=False),
        sa.Column('mood', sa.Integer(), nullable=True),
        sa.Column('energy', sa.Integer(), nullable=True),
        sa.Column('sleep_quality', sa.Integer(), nullable=True),
        sa.Column('side_effects_other', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('mood IS NULL OR mood BETWEEN 1 AND 5', name='ck_journal_entry_mood_range'),
        sa.CheckConstraint('energy IS NULL OR energy BETWEEN 1 AND 5', name='ck_journal_entry_energy_range'),
        sa.CheckConstraint('sleep_quality IS NULL OR sleep_quality BETWEEN 1 AND 5', name='ck_journal_entry_sleep_range'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_id', 'entry_date', name='uq_journal_entry_owner_date'),
    )
    op.create_index('ix_journal_entries_owner_id', 'journal_entries', ['owner_id'])
    op.create_index('ix_journal_entries_entry_date', 'journal_entries', ['entry_date'])

    op.create_table(
        'journal_entry_side_effects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('entry_id', sa.Integer(), nullable=False),
        sa.Column('side_effect', sa.Enum(
            'Injection site reaction', 'Headache', 'Nausea', 'Fatigue',
            'Bloating / water retention', 'GI upset', 'Joint pain', 'Insomnia',
            'Appetite change', 'Flushing / dizziness',
            name='journal_side_effect', native_enum=False, length=40), nullable=False),
        sa.ForeignKeyConstraint(['entry_id'], ['journal_entries.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('entry_id', 'side_effect', name='uq_journal_entry_side_effect'),
    )
    op.create_index('ix_journal_entry_side_effects_entry_id', 'journal_entry_side_effects', ['entry_id'])

    op.create_table(
        'journal_quick_notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('entry_id', sa.Integer(), nullable=False),
        sa.Column('noted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['entry_id'], ['journal_entries.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_journal_quick_notes_entry_id', 'journal_quick_notes', ['entry_id'])


def downgrade() -> None:
    op.drop_index('ix_journal_quick_notes_entry_id', table_name='journal_quick_notes')
    op.drop_table('journal_quick_notes')
    op.drop_index('ix_journal_entry_side_effects_entry_id', table_name='journal_entry_side_effects')
    op.drop_table('journal_entry_side_effects')
    op.drop_index('ix_journal_entries_entry_date', table_name='journal_entries')
    op.drop_index('ix_journal_entries_owner_id', table_name='journal_entries')
    op.drop_table('journal_entries')
