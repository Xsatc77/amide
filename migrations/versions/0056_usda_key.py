"""settings: own USDA FoodData Central key

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0056'
down_revision: Union[str, None] = '0055'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('usda_api_key', sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('usda_api_key')
