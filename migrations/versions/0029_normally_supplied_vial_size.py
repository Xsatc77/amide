"""peptides.normally_supplied_amount and normally_supplied_unit: standard vial size for course totals fallback

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0029'
down_revision: Union[str, None] = '0028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('peptides', schema=None) as batch_op:
        batch_op.add_column(sa.Column('normally_supplied_amount', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('normally_supplied_unit', sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('peptides', schema=None) as batch_op:
        batch_op.drop_column('normally_supplied_unit')
        batch_op.drop_column('normally_supplied_amount')
