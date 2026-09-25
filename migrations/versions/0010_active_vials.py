"""active vials: ActiveVial table, User.default_discard_days

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOSE_UNIT_VALUES = ('mg', 'mcg', 'IU')


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('default_discard_days', sa.Integer(), nullable=True))

    op.create_table(
        'active_vials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('inventory_item_id', sa.Integer(), nullable=False),
        sa.Column('concentration_mg_ml', sa.Float(), nullable=False),
        sa.Column('water_ml', sa.Float(), nullable=False),
        sa.Column('dose_value', sa.Float(), nullable=False),
        sa.Column('dose_unit', sa.Enum(*DOSE_UNIT_VALUES, name='doseunit', native_enum=False, length=20),
                  nullable=False),
        sa.Column('doses_total', sa.Integer(), nullable=False),
        sa.Column('date_mixed', sa.Date(), nullable=False),
        sa.Column('discard_by', sa.Date(), nullable=False),
        sa.Column('discarded_at', sa.DateTime(), nullable=True),
        sa.Column('last_discard_prompt_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_active_vials_owner_id', 'active_vials', ['owner_id'])
    op.create_index('ix_active_vials_inventory_item_id', 'active_vials', ['inventory_item_id'])


def downgrade() -> None:
    op.drop_index('ix_active_vials_inventory_item_id', table_name='active_vials')
    op.drop_index('ix_active_vials_owner_id', table_name='active_vials')
    op.drop_table('active_vials')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('default_discard_days')
