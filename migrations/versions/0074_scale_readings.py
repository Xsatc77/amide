"""body_measurements: the numbers a body-composition scale reports

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0074'
down_revision: Union[str, None] = '0073'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FLOATS = (
    'scale_bmi', 'scale_body_fat_pct', 'scale_fat_mass_lb', 'scale_muscle_pct', 'scale_muscle_mass_lb', 'scale_skeletal_muscle_pct',
    'scale_skeletal_muscle_mass_lb', 'scale_bone_pct', 'scale_bone_mass_lb', 'scale_protein_pct', 'scale_protein_mass_lb', 'scale_water_pct',
    'scale_water_mass_lb', 'scale_fat_free_mass_lb', 'scale_subcutaneous_fat_pct', 'scale_visceral_fat', 'scale_bmr_kcal', 'scale_metabolic_age', 'scale_whr',
)


def upgrade() -> None:
    with op.batch_alter_table('body_measurements') as batch:
        batch.add_column(sa.Column('scale_stamp', sa.String(length=30), nullable=True))
        for name in FLOATS:
            batch.add_column(sa.Column(name, sa.Float(), nullable=True))
    op.create_index('ix_body_measurements_scale_stamp', 'body_measurements', ['scale_stamp'])


def downgrade() -> None:
    op.drop_index('ix_body_measurements_scale_stamp', table_name='body_measurements')
    with op.batch_alter_table('body_measurements') as batch:
        for name in reversed(FLOATS):
            batch.drop_column(name)
        batch.drop_column('scale_stamp')
