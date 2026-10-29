"""learning notes: a note may belong to no peptide (a general note)

Revision ID: 0082
Revises: 0081
Create Date: 2026-10-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0082'
down_revision: Union[str, None] = '0081'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('peptide_notes') as b:
        b.alter_column('peptide_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM peptide_notes WHERE peptide_id IS NULL")      # a general note has no card to go back to
    with op.batch_alter_table('peptide_notes') as b:
        b.alter_column('peptide_id', existing_type=sa.Integer(), nullable=False)
