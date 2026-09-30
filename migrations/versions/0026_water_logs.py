"""water_logs: one row per "+ Log water" tap on the Dashboard's Water goal panel

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0026'
down_revision: Union[str, None] = '0025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'water_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('logged_at', sa.Date(), nullable=False),
        sa.Column('ounces', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('ounces > 0', name='ck_water_log_ounces_pos'),
    )
    op.create_index('ix_water_logs_owner_id', 'water_logs', ['owner_id'])
    op.create_index('ix_water_logs_logged_at', 'water_logs', ['logged_at'])


def downgrade() -> None:
    op.drop_table('water_logs')
