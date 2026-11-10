"""measurements: the time an entry was recorded, so a second reading within 12 hours can use the original

Revision ID: 0093
Revises: 0092
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0093'
down_revision: Union[str, None] = '0092'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('body_measurements') as batch:
        batch.add_column(sa.Column('recorded_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('body_measurements') as batch:
        batch.drop_column('recorded_at')
