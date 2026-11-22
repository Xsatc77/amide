"""workout_exercise_logs.skipped and skip_reason: an exercise left out on purpose, and why

Upgrade: adds both columns; every logged exercise so far is not skipped.

Downgrade: drops them (an older Amide has no skip, so a skipped exercise then reads as one that was simply not done).

Revision ID: 0109
Revises: 0108
Create Date: 2026-11-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0109'
down_revision: Union[str, None] = '0108'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("workout_exercise_logs") as batch:
        batch.add_column(sa.Column("skipped", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("skip_reason", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workout_exercise_logs") as batch:
        batch.drop_column("skip_reason")
        batch.drop_column("skipped")
