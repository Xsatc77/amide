"""workouts: a demo video link on each plan exercise

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0061'
down_revision: Union[str, None] = '0060'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('workout_exercises', sa.Column('video_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('workout_exercises') as batch:
        batch.drop_column('video_url')
