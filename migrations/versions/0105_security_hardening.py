"""installation_settings (New User switch) and security_events (the security log)

Revision ID: 0105
Revises: 0104
Create Date: 2026-11-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0105'
down_revision: Union[str, None] = '0104'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'installation_settings',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('value', sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_table(
        'security_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('username', sa.String(length=64), nullable=True),
        sa.Column('event', sa.String(length=40), nullable=False),
        sa.Column('detail', sa.String(length=200), nullable=False),
        sa.Column('client', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_security_events_created_at', 'security_events', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_security_events_created_at', table_name='security_events')
    op.drop_table('security_events')
    op.drop_table('installation_settings')
