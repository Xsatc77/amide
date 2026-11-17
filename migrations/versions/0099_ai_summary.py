"""ai_summary: a person's AI agent settings, and the saved Progress Review summaries

Revision ID: 0099
Revises: 0098
Create Date: 2026-11-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0099'
down_revision: Union[str, None] = '0098'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('ai_provider', sa.String(12), nullable=True))
        batch.add_column(sa.Column('ai_model', sa.String(80), nullable=True))
        batch.add_column(sa.Column('ai_base_url', sa.String(200), nullable=True))
        batch.add_column(sa.Column('ai_api_key', sa.String(300), nullable=True))
    op.create_table(
        'progress_summaries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('provider', sa.String(12), nullable=False),
        sa.Column('model', sa.String(80), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('owner_id', 'start_date', 'end_date', name='uq_progress_summary_range'),
    )


def downgrade() -> None:
    op.drop_table('progress_summaries')
    with op.batch_alter_table('users') as batch:
        for column in ('ai_api_key', 'ai_base_url', 'ai_model', 'ai_provider'):
            batch.drop_column(column)
