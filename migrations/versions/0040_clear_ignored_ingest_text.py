"""clear the text kept for ignored (not price list) chat messages

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0040'
down_revision: Union[str, None] = '0039'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CLEAR_SQL = "UPDATE ingest_items SET caption = NULL WHERE status = 'ignored'"


def upgrade() -> None:
    op.execute(CLEAR_SQL)


def downgrade() -> None:
    pass                            # the text is gone on purpose
