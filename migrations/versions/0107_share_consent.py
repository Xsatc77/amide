"""a share needs the grantee's consent: shares.accepted_at

Upgrade: adds shares.accepted_at (NULL = pending). Every share that exists now is marked accepted at the moment of the upgrade, so
nobody loses or gains access when Amide is updated; only shares made afterwards start pending.

Downgrade: drops the column. Pending shares then become live shares (an older Amide has no consent step), so they are deleted first.

Revision ID: 0107
Revises: 0106
Create Date: 2026-11-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0107'
down_revision: Union[str, None] = '0106'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shares") as batch:
        batch.add_column(sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE shares SET accepted_at = CURRENT_TIMESTAMP")


def downgrade() -> None:
    op.execute("DELETE FROM shares WHERE accepted_at IS NULL")
    with op.batch_alter_table("shares") as batch:
        batch.drop_column("accepted_at")
