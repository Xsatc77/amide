"""inventory: the lot number a reconstituted vial came from

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0064'
down_revision: Union[str, None] = '0063'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('active_vials', sa.Column('lot_number', sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('active_vials') as batch:
        batch.drop_column('lot_number')
