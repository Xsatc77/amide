"""users: the secret token of the private calendar feed

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0048'
down_revision: Union[str, None] = '0047'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('calendar_token', sa.String(64), nullable=True))
    op.create_index('ix_users_calendar_token', 'users', ['calendar_token'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_users_calendar_token', table_name='users')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('calendar_token')
