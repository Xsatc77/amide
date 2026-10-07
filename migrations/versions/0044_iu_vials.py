"""IU vials: the vial's unit and IU-per-mg factor on active vials, and the factor on inventory items

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0044'
down_revision: Union[str, None] = '0043'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('inventory_items', sa.Column('iu_per_mg', sa.Float()))
    op.add_column('active_vials', sa.Column('vial_unit', sa.String(10), nullable=False, server_default='mg'))
    op.add_column('active_vials', sa.Column('iu_per_mg', sa.Float()))


def downgrade() -> None:
    with op.batch_alter_table('active_vials') as batch:
        batch.drop_column('iu_per_mg')
        batch.drop_column('vial_unit')
    with op.batch_alter_table('inventory_items') as batch:
        batch.drop_column('iu_per_mg')
