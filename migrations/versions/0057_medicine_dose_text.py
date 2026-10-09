"""settings: optional dose text on a listed medicine

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0057'
down_revision: Union[str, None] = '0056'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_medicines', sa.Column('dose_text', sa.String(80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('user_medicines') as batch:
        batch.drop_column('dose_text')
