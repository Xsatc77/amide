"""peptide sheets: add tags and summary columns

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0021'
down_revision: Union[str, None] = '0020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Plain nullable column additions with no other change to the table: SQLite handles these with a
# native ALTER TABLE ADD COLUMN (no reflect-and-rebuild), so unlike 0020's or 0016's raw-SQL
# rebuilds, batch_alter_table here does NOT touch (and so cannot drop) peptides.name's
# COLLATE NOCASE -- verified directly against a real migrated db before relying on it.
COLUMNS = [
    sa.Column('tags', sa.JSON(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
]


def upgrade() -> None:
    with op.batch_alter_table('peptides', schema=None) as batch_op:
        for column in COLUMNS:
            batch_op.add_column(column.copy())


def downgrade() -> None:
    with op.batch_alter_table('peptides', schema=None) as batch_op:
        for column in reversed(COLUMNS):
            batch_op.drop_column(column.name)
