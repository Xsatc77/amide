"""settings: each person's medicine list, for peptide cautions

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0053'
down_revision: Union[str, None] = '0052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_medicines',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('name', sa.String(80), nullable=False),
        sa.Column('notes', sa.String(200), nullable=True),
        sa.UniqueConstraint('owner_id', 'name', name='uq_user_medicine'),
    )


def downgrade() -> None:
    op.drop_table('user_medicines')
