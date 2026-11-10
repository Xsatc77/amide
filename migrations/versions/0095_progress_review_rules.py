"""users: their own thresholds for the Progress Review flags

Revision ID: 0095
Revises: 0094
Create Date: 2026-11-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0095'
down_revision: Union[str, None] = '0094'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (('review_loss_pct', sa.Float), ('review_protein_g_per_kg', sa.Float), ('review_sleep_low', sa.Integer),
           ('review_sleep_share_pct', sa.Float), ('review_bp_drop', sa.Integer), ('review_retest_days', sa.Integer))


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        for name, kind in COLUMNS:
            batch.add_column(sa.Column(name, kind(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        for name, _ in reversed(COLUMNS):
            batch.drop_column(name)
