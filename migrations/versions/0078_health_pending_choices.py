"""apple health: pending metric choices (decided_at may be empty: received, not yet decided)

Revision ID: 0078
Revises: 0077
Create Date: 2026-10-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0078'
down_revision: Union[str, None] = '0077'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('health_metric_choices') as b:
        b.alter_column('decided_at', existing_type=sa.DateTime(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM health_metric_choices WHERE decided_at IS NULL")      # pending rows were never answers
    with op.batch_alter_table('health_metric_choices') as b:
        b.alter_column('decided_at', existing_type=sa.DateTime(), nullable=False)
