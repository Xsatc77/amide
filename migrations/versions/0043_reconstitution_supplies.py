"""supply types, BAC priority, and open BAC water vials (active vials without a concentration or doses)

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0043'
down_revision: Union[str, None] = '0042'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SUPPLY_TYPES = ('reconstitution_syringe', 'dosing_syringe', 'alcohol_prep_pad', 'peptide_pen_vial', 'peptide_pen_needle',
                'sterile_vial_10ml', 'peptide_filter')


def upgrade() -> None:
    op.add_column('inventory_items', sa.Column('supply_type', sa.String(30)))
    op.add_column('inventory_items', sa.Column('bac_priority', sa.Integer()))
    with op.batch_alter_table('active_vials') as batch:
        batch.alter_column('concentration_mg_ml', existing_type=sa.Float(), nullable=True)
        batch.alter_column('dose_value', existing_type=sa.Float(), nullable=True)
        batch.alter_column('dose_unit', existing_type=sa.String(20), nullable=True)
        batch.alter_column('doses_total', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM active_vials WHERE concentration_mg_ml IS NULL")      # open BAC vials cannot exist in the old shape
    with op.batch_alter_table('active_vials') as batch:
        batch.alter_column('doses_total', existing_type=sa.Integer(), nullable=False)
        batch.alter_column('dose_unit', existing_type=sa.String(20), nullable=False)
        batch.alter_column('dose_value', existing_type=sa.Float(), nullable=False)
        batch.alter_column('concentration_mg_ml', existing_type=sa.Float(), nullable=False)
    with op.batch_alter_table('inventory_items') as batch:
        batch.drop_column('bac_priority')
        batch.drop_column('supply_type')
