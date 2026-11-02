"""measurements: a short note per measurement

Revision ID: 0084
Revises: 0083
Create Date: 2026-11-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0084'
down_revision: Union[str, None] = '0083'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('body_measurements') as batch:
        batch.add_column(sa.Column('notes', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('body_measurements') as batch:
        batch.drop_column('notes')
