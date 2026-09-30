"""protocol_item_cycle_offs: week-ranges during which a protocol item is never due

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0027'
down_revision: Union[str, None] = '0026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'protocol_item_cycle_offs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('protocol_item_id', sa.Integer(), sa.ForeignKey('protocol_items.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('start_week', sa.Integer(), nullable=False),
        sa.Column('end_week', sa.Integer(), nullable=False),
        sa.CheckConstraint('start_week >= 1', name='ck_cycle_off_start_week'),
        sa.CheckConstraint('end_week >= start_week', name='ck_cycle_off_end_week'),
    )
    op.create_index('ix_protocol_item_cycle_offs_protocol_item_id', 'protocol_item_cycle_offs', ['protocol_item_id'])


def downgrade() -> None:
    op.drop_table('protocol_item_cycle_offs')
