"""open BAC Water bottles are good for 90 days, not 28

Revision ID: 0092
Revises: 0091
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0092'
down_revision: Union[str, None] = '0091'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Only the bottles still open and still on the old 28-day date; a date the user set by hand is left alone.
    op.execute(
        "UPDATE active_vials SET discard_by = date(date_mixed, '+90 days') "
        "WHERE discarded_at IS NULL AND discard_by = date(date_mixed, '+28 days') "
        "AND inventory_item_id IN (SELECT id FROM inventory_items WHERE category = 'BAC Water')")


def downgrade() -> None:
    pass
