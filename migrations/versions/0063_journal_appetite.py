"""journal: an appetite rating (1 to 5) on each entry

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0063'
down_revision: Union[str, None] = '0062'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('journal_entries') as batch:
        batch.add_column(sa.Column('appetite', sa.Integer(), nullable=True))
        batch.create_check_constraint('ck_journal_entry_appetite_range', 'appetite IS NULL OR appetite BETWEEN 1 AND 5')


def downgrade() -> None:
    with op.batch_alter_table('journal_entries') as batch:
        batch.drop_constraint('ck_journal_entry_appetite_range', type_='check')
        batch.drop_column('appetite')
