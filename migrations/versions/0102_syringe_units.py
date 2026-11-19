"""inventory_items.syringe_units_per_ml: the units to a mL on a dosing syringe (blank is U-100)

Revision ID: 0102
Revises: 0101
Create Date: 2026-11-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0102'
down_revision: Union[str, None] = '0101'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('inventory_items') as batch:
        batch.add_column(sa.Column('syringe_units_per_ml', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('inventory_items') as batch:
        batch.drop_column('syringe_units_per_ml')
