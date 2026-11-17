"""ai_notes: the notes a person keeps on the Progress Review page for the AI summary to take into account

Revision ID: 0100
Revises: 0099
Create Date: 2026-11-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0100'
down_revision: Union[str, None] = '0099'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('ai_notes', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('ai_notes')
