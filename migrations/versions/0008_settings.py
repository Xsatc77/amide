"""settings: email, timezone, colorway

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLORWAY_VALUES = ('light', 'dark', 'tequila_sunrise', 'fireworks', 'solarin', 'bricks', 'retro',
                   'greensleeves', 'high_contrast')


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email', sa.String(length=320), nullable=True))
        batch_op.add_column(sa.Column('timezone', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('colorway', sa.Enum(*COLORWAY_VALUES, name='colorway',
                                                          native_enum=False, length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('colorway')
        batch_op.drop_column('timezone')
        batch_op.drop_column('email')
