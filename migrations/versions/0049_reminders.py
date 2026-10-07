"""users: ntfy reminder settings, and the reminders already sent

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0049'
down_revision: Union[str, None] = '0048'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('ntfy_enabled', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('ntfy_topic', sa.String(100), nullable=True))
    op.create_table(
        'dose_reminders',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('protocol_item_id', sa.Integer(), nullable=False),
        sa.Column('for_date', sa.Date(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('user_id', 'protocol_item_id', 'for_date', name='uq_dose_reminder'),
    )


def downgrade() -> None:
    op.drop_table('dose_reminders')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('ntfy_topic')
        batch.drop_column('ntfy_enabled')
