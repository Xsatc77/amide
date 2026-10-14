"""dosing: remember which supplies a logged shot used up, so undoing it can give them back

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0067'
down_revision: Union[str, None] = '0066'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('dose_logs', sa.Column('supplies_used', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('dose_logs') as batch:
        batch.drop_column('supplies_used')
