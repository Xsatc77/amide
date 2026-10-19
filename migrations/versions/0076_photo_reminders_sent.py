"""photo_reminders_sent: body-photo reminders already pushed

Revision ID: 0076
Revises: 0075
Create Date: 2026-10-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0076'
down_revision: Union[str, None] = '0075'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'photo_reminders_sent',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('reminder_key', sa.String(60), nullable=False),
        sa.Column('for_date', sa.Date(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'reminder_key', 'for_date', name='uq_photo_reminder_sent'),
    )
    op.create_index('ix_photo_reminders_sent_user_id', 'photo_reminders_sent', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_photo_reminders_sent_user_id', table_name='photo_reminders_sent')
    op.drop_table('photo_reminders_sent')
