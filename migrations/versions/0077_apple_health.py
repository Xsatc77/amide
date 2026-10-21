"""apple health: raw uploads, readings, events, metric choices, and the token scope

Revision ID: 0077
Revises: 0076
Create Date: 2026-10-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0077'
down_revision: Union[str, None] = '0076'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('ingest_tokens') as b:
        b.add_column(sa.Column('scope', sa.String(12), nullable=False, server_default='watcher'))
    op.create_table(
        'health_raw',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('file_name', sa.String(80), nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('note', sa.String(300)),
        sa.Column('automation_name', sa.String(120)),
        sa.Column('readings_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('headers', sa.JSON()),
        sa.UniqueConstraint('owner_id', 'sha256', name='uq_health_raw_sha'),
        sa.CheckConstraint("status IN ('stored', 'parsed', 'flagged')", name='ck_health_raw_status'),
    )
    op.create_index('ix_health_raw_owner_id', 'health_raw', ['owner_id'])
    op.create_table(
        'health_readings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('metric', sa.String(80), nullable=False),
        sa.Column('start_at', sa.DateTime(), nullable=False),
        sa.Column('end_at', sa.DateTime(), nullable=False),
        sa.Column('offset_minutes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('grain', sa.String(8), nullable=False),
        sa.Column('value', sa.Float()),
        sa.Column('unit', sa.String(30)),
        sa.Column('source', sa.String(120), nullable=False, server_default=''),
        sa.Column('extra', sa.JSON()),
        sa.UniqueConstraint('owner_id', 'metric', 'start_at', 'grain', 'source', name='uq_health_reading'),
        sa.CheckConstraint("grain IN ('second', 'minute', 'hour', 'day', 'point')", name='ck_health_reading_grain'),
    )
    op.create_index('ix_health_readings_owner_id', 'health_readings', ['owner_id'])
    op.create_index('ix_health_reading_owner_metric_start', 'health_readings', ['owner_id', 'metric', 'start_at'])
    op.create_table(
        'health_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('external_id', sa.String(120), nullable=False),
        sa.Column('start_at', sa.DateTime(), nullable=False),
        sa.Column('end_at', sa.DateTime()),
        sa.Column('name', sa.String(120)),
        sa.Column('payload', sa.JSON()),
        sa.UniqueConstraint('owner_id', 'kind', 'external_id', name='uq_health_event'),
    )
    op.create_index('ix_health_events_owner_id', 'health_events', ['owner_id'])
    op.create_table(
        'health_metric_choices',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('metric', sa.String(80), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('owner_id', 'metric', name='uq_health_metric_choice'),
    )
    op.create_index('ix_health_metric_choices_owner_id', 'health_metric_choices', ['owner_id'])


def downgrade() -> None:
    op.drop_index('ix_health_metric_choices_owner_id', table_name='health_metric_choices')
    op.drop_table('health_metric_choices')
    op.drop_index('ix_health_events_owner_id', table_name='health_events')
    op.drop_table('health_events')
    op.drop_index('ix_health_reading_owner_metric_start', table_name='health_readings')
    op.drop_index('ix_health_readings_owner_id', table_name='health_readings')
    op.drop_table('health_readings')
    op.drop_index('ix_health_raw_owner_id', table_name='health_raw')
    op.drop_table('health_raw')
    with op.batch_alter_table('ingest_tokens') as b:
        b.drop_column('scope')
