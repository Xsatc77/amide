"""labs: LabPanel and LabResult

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0019'
down_revision: Union[str, None] = '0018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'lab_panels',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('drawn_at', sa.Date(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('report_filename', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_lab_panels_owner_id', 'lab_panels', ['owner_id'])
    op.create_index('ix_lab_panels_drawn_at', 'lab_panels', ['drawn_at'])

    op.create_table(
        'lab_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('panel_id', sa.Integer(), nullable=False),
        sa.Column('marker', sa.Enum(
            'Total Testosterone', 'Free Testosterone', 'Estradiol', 'LH', 'FSH', 'SHBG',
            'Prolactin', 'IGF-1', 'Cortisol', 'Fasting Glucose', 'HbA1c', 'Fasting Insulin',
            'Total Cholesterol', 'LDL', 'HDL', 'Triglycerides', 'TSH', 'Free T3', 'Free T4',
            'ALT', 'AST', 'Creatinine', 'eGFR', 'BUN', 'Hemoglobin', 'Hematocrit', 'WBC',
            'Platelets', 'hs-CRP', 'Vitamin D', 'Ferritin', 'Other',
            name='lab_marker', native_enum=False, length=25), nullable=False),
        sa.Column('marker_other', sa.String(length=80), nullable=True),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('unit', sa.String(length=20), nullable=True),
        sa.Column('range_low', sa.Float(), nullable=True),
        sa.Column('range_high', sa.Float(), nullable=True),
        sa.CheckConstraint('range_low IS NULL OR range_high IS NULL OR range_low <= range_high',
                            name='ck_lab_result_range_order'),
        sa.ForeignKeyConstraint(['panel_id'], ['lab_panels.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_lab_results_panel_id', 'lab_results', ['panel_id'])


def downgrade() -> None:
    op.drop_index('ix_lab_results_panel_id', table_name='lab_results')
    op.drop_table('lab_results')
    op.drop_index('ix_lab_panels_drawn_at', table_name='lab_panels')
    op.drop_index('ix_lab_panels_owner_id', table_name='lab_panels')
    op.drop_table('lab_panels')
