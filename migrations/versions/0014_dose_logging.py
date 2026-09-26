"""dose logging: DoseLog table, ActiveVial dispensing_method + volume_remaining_ml

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = '0014'
down_revision: Union[str, None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        'dose_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('protocol_id', sa.Integer(), nullable=False),
        sa.Column('protocol_item_id', sa.Integer(), nullable=True),
        sa.Column('active_vial_id', sa.Integer(), nullable=True),
        sa.Column('peptide_id', sa.Integer(), nullable=False),
        sa.Column('peptide_name', sa.String(length=120), nullable=False),
        sa.Column('dose_value', sa.Float(), nullable=True),
        sa.Column('dose_unit', sa.Enum('mg', 'mcg', 'IU', name='dose_unit', native_enum=False, length=20), nullable=False),
        sa.Column('route', sa.String(length=20), nullable=False),
        sa.Column('scheduled_date', sa.Date(), nullable=False),
        sa.Column('scheduled_time_of_day', sa.Enum('am', 'pm', 'bedtime', 'any', name='time_of_day', native_enum=False, length=20), nullable=False),
        sa.Column('status', sa.Enum('on_time', 'late', 'missed', 'skipped', name='dose_status', native_enum=False, length=20), nullable=False),
        sa.Column('logged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('injection_site', sa.Enum('abdomen_l', 'abdomen_r', 'thigh_l', 'thigh_r', 'arm_l', 'arm_r', 'glute_l', 'glute_r', name='injection_site', native_enum=False, length=20), nullable=True),
        sa.Column('volume_ml', sa.Float(), nullable=True),
        sa.CheckConstraint('dose_value IS NULL OR dose_value > 0', name='ck_dose_log_dose_pos'),
        sa.CheckConstraint('volume_ml IS NULL OR volume_ml > 0', name='ck_dose_log_volume_pos'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.ForeignKeyConstraint(['protocol_id'], ['protocols.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['protocol_item_id'], ['protocol_items.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['active_vial_id'], ['active_vials.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['peptide_id'], ['peptides.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dose_logs_owner_id', 'dose_logs', ['owner_id'])
    op.create_index('ix_dose_logs_protocol_id', 'dose_logs', ['protocol_id'])

    with op.batch_alter_table('active_vials', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'dispensing_method', sa.Enum('syringe', 'pen', name='dispensing_method', native_enum=False, length=20),
            nullable=False, server_default='syringe'))
        batch_op.add_column(sa.Column('volume_remaining_ml', sa.Float(), nullable=True))

    bind.execute(text("UPDATE active_vials SET volume_remaining_ml = water_ml"))

    with op.batch_alter_table('active_vials', schema=None) as batch_op:
        batch_op.alter_column('volume_remaining_ml', nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('active_vials', schema=None) as batch_op:
        batch_op.drop_column('volume_remaining_ml')
        batch_op.drop_column('dispensing_method')
    op.drop_index('ix_dose_logs_protocol_id', table_name='dose_logs')
    op.drop_index('ix_dose_logs_owner_id', table_name='dose_logs')
    op.drop_table('dose_logs')
