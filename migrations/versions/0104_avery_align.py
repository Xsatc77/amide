"""users.avery_align: how far a person's printer is off the Avery 94500 circles, so Amide can move the print to match

Revision ID: 0104
Revises: 0103
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0104'
down_revision: Union[str, None] = '0103'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('avery_align', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('avery_align')
