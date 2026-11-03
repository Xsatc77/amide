"""protocol items: how often each item of a short course joins in (12, 6 or 3 months)

Revision ID: 0086
Revises: 0085
Create Date: 2026-11-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0086'
down_revision: Union[str, None] = '0085'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('protocol_items') as batch:
        batch.add_column(sa.Column('repeat_months', sa.Integer(), nullable=True))
    # A short course saved before this change repeated as a whole: every item takes its protocol's cadence.
    op.execute("UPDATE protocol_items SET repeat_months = "
               "(SELECT repeat_months FROM protocols WHERE protocols.id = protocol_items.protocol_id) "
               "WHERE run_days IS NOT NULL")


def downgrade() -> None:
    with op.batch_alter_table('protocol_items') as batch:
        batch.drop_column('repeat_months')
