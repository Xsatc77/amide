"""price list ingest: tokens, chat sources, received items, dashboard dismissals

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0039'
down_revision: Union[str, None] = '0038'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ingest_tokens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('label', sa.String(60), nullable=False),
        sa.Column('prefix', sa.String(24), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime()),
        sa.Column('revoked_at', sa.DateTime()),
    )
    op.create_index('ix_ingest_tokens_owner_id', 'ingest_tokens', ['owner_id'])
    op.create_table(
        'ingest_sources',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('platform', sa.String(20), nullable=False),
        sa.Column('chat_id', sa.String(64), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('vendor_id', sa.Integer(), sa.ForeignKey('vendors.id', ondelete='SET NULL')),
        sa.Column('default_warehouse', sa.String(10)),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('state', sa.String(10), nullable=False),
        sa.Column('state_reason', sa.String(200)),
        sa.Column('state_changed_at', sa.DateTime()),
        sa.Column('alert_acknowledged_at', sa.DateTime()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('platform', 'chat_id', name='uq_ingest_source_chat'),
        sa.CheckConstraint("default_warehouse IS NULL OR default_warehouse IN ('us', 'china')", name='ck_ingest_source_warehouse'),
        sa.CheckConstraint("state IN ('active', 'gone')", name='ck_ingest_source_state'),
    )
    op.create_table(
        'ingest_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source_id', sa.Integer(), sa.ForeignKey('ingest_sources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('message_id', sa.String(64), nullable=False),
        sa.Column('album_id', sa.String(64)),
        sa.Column('group_key', sa.String(160), nullable=False),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.Column('filename', sa.String(200)),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('file_hash', sa.String(64), nullable=False),
        sa.Column('stored_file', sa.String(64)),
        sa.Column('caption', sa.Text()),
        sa.Column('status', sa.String(15), nullable=False),
        sa.Column('reason', sa.String(300)),
        sa.Column('vendor_id', sa.Integer(), sa.ForeignKey('vendors.id', ondelete='SET NULL')),
        sa.Column('warehouse', sa.String(10)),
        sa.Column('list_date', sa.Date()),
        sa.Column('price_list_id', sa.Integer(), sa.ForeignKey('price_lists.id', ondelete='SET NULL')),
        sa.Column('rows_found', sa.Integer()),
        sa.Column('rows_matched', sa.Integer()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('decided_at', sa.DateTime()),
        sa.Column('decided_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.UniqueConstraint('source_id', 'message_id', 'file_hash', name='uq_ingest_item_message_file'),
        sa.CheckConstraint("kind IN ('pdf', 'image', 'xlsx', 'text')", name='ck_ingest_item_kind'),
        sa.CheckConstraint("status IN ('received', 'imported', 'needs_review', 'ignored', 'duplicate', 'failed', 'undone', 'rejected')",
                           name='ck_ingest_item_status'),
    )
    op.create_index('ix_ingest_items_source_id', 'ingest_items', ['source_id'])
    op.create_index('ix_ingest_items_group_key', 'ingest_items', ['group_key'])
    op.create_table(
        'dashboard_dismissals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('alert_key', sa.String(120), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('user_id', 'alert_key', name='uq_dashboard_dismissal'),
    )
    op.create_index('ix_dashboard_dismissals_user_id', 'dashboard_dismissals', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_dashboard_dismissals_user_id', table_name='dashboard_dismissals')
    op.drop_table('dashboard_dismissals')
    op.drop_index('ix_ingest_items_group_key', table_name='ingest_items')
    op.drop_index('ix_ingest_items_source_id', table_name='ingest_items')
    op.drop_table('ingest_items')
    op.drop_table('ingest_sources')
    op.drop_index('ix_ingest_tokens_owner_id', table_name='ingest_tokens')
    op.drop_table('ingest_tokens')
