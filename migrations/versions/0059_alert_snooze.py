"""dashboard: an alert dismissal can expire (snooze)

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0059'
down_revision: Union[str, None] = '0058'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('dashboard_dismissals', sa.Column('snoozed_until', sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('dashboard_dismissals') as batch:
        batch.drop_column('snoozed_until')
