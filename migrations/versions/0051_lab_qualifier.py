"""lab_results: qualifier (< or >) for results reported as less than or greater than a number

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0051'
down_revision: Union[str, None] = '0050'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('lab_results', sa.Column('qualifier', sa.String(1), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('lab_results') as batch:
        batch.drop_column('qualifier')
