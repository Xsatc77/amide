"""ingest topics, selected-topics switch, skip words and auto-follow words

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0041'
down_revision: Union[str, None] = '0040'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ingest_topics',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source_id', sa.Integer(), sa.ForeignKey('ingest_sources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('topic_id', sa.String(32), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('source_id', 'topic_id', name='uq_ingest_topic'),
    )
    op.create_index('ix_ingest_topics_source_id', 'ingest_topics', ['source_id'])
    op.add_column('ingest_sources', sa.Column('topics_only', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('ingest_sources', sa.Column('skip_words', sa.String(1200)))
    op.add_column('ingest_sources', sa.Column('follow_words', sa.String(1200), server_default='price, prices, pricing, pricelist, warehouse'))
    op.add_column('ingest_items', sa.Column('topic_id', sa.String(32)))
    op.add_column('ingest_items', sa.Column('topic_title', sa.String(200)))


def downgrade() -> None:
    with op.batch_alter_table('ingest_items') as batch:
        batch.drop_column('topic_title')
        batch.drop_column('topic_id')
    with op.batch_alter_table('ingest_sources') as batch:
        batch.drop_column('follow_words')
        batch.drop_column('skip_words')
        batch.drop_column('topics_only')
    op.drop_index('ix_ingest_topics_source_id', table_name='ingest_topics')
    op.drop_table('ingest_topics')
