"""peptide sheets: add sheet_sections_simple column

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0023'
down_revision: Union[str, None] = '0022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Plain nullable column addition with no other change to the table: SQLite handles this with a
# native ALTER TABLE ADD COLUMN (no reflect-and-rebuild), so batch_alter_table here does NOT touch
# (and so cannot drop) peptides.name's COLLATE NOCASE -- same reasoning as 0021.
COLUMNS = [
    sa.Column('sheet_sections_simple', sa.JSON(), nullable=True),
]


def upgrade() -> None:
    with op.batch_alter_table('peptides', schema=None) as batch_op:
        for column in COLUMNS:
            batch_op.add_column(column.copy())


def downgrade() -> None:
    with op.batch_alter_table('peptides', schema=None) as batch_op:
        for column in reversed(COLUMNS):
            batch_op.drop_column(column.name)
