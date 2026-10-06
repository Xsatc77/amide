"""body_photos table, users.photo_2fa_required, sessions.photo_unlocked_until

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0037'
down_revision: Union[str, None] = '0036'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'body_photos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('taken_on', sa.Date(), nullable=False),
        sa.Column('angle', sa.String(10)),
        sa.Column('note', sa.String(200)),
        sa.Column('filename', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("angle IS NULL OR angle IN ('front', 'side', 'back', 'other')", name='ck_body_photo_angle'),
    )
    op.create_index('ix_body_photos_owner_id', 'body_photos', ['owner_id'])
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('photo_2fa_required', sa.Boolean(), nullable=False, server_default='0'))
    with op.batch_alter_table('sessions') as batch_op:
        batch_op.add_column(sa.Column('photo_unlocked_until', sa.DateTime()))


def downgrade() -> None:
    with op.batch_alter_table('sessions') as batch_op:
        batch_op.drop_column('photo_unlocked_until')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('photo_2fa_required')
    op.drop_index('ix_body_photos_owner_id', table_name='body_photos')
    op.drop_table('body_photos')
