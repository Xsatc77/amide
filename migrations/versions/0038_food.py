"""foods and food_logs

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0038'
down_revision: Union[str, None] = '0037'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'foods',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE')),
        sa.Column('source', sa.String(10), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('serving', sa.String(60), nullable=False),
        sa.Column('serving_g', sa.Float()),
        sa.Column('calories', sa.Float(), nullable=False),
        sa.Column('protein_g', sa.Float(), nullable=False),
        sa.Column('carb_g', sa.Float(), nullable=False),
        sa.Column('fat_g', sa.Float(), nullable=False),
        sa.Column('fiber_g', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('owner_id', 'name', 'serving', name='uq_food_owner_name_serving'),
        sa.CheckConstraint("source IN ('starter', 'mine')", name='ck_food_source'),
        sa.CheckConstraint("(source = 'starter' AND owner_id IS NULL) OR (source = 'mine' AND owner_id IS NOT NULL)",
                           name='ck_food_owner_source'),
        sa.CheckConstraint('calories >= 0 AND protein_g >= 0 AND carb_g >= 0 AND fat_g >= 0 AND fiber_g >= 0',
                           name='ck_food_nonnegative'),
    )
    op.create_index('ix_foods_owner_id', 'foods', ['owner_id'])
    op.create_index('uq_food_starter_name_serving', 'foods', ['name', 'serving'], unique=True,
                    sqlite_where=sa.text('owner_id IS NULL'))
    op.create_table(
        'food_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('eaten_on', sa.Date(), nullable=False),
        sa.Column('meal', sa.String(10), nullable=False),
        sa.Column('food_id', sa.Integer(), sa.ForeignKey('foods.id', ondelete='SET NULL')),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('serving', sa.String(60), nullable=False),
        sa.Column('servings', sa.Float(), nullable=False),
        sa.Column('calories', sa.Float(), nullable=False),
        sa.Column('protein_g', sa.Float(), nullable=False),
        sa.Column('carb_g', sa.Float(), nullable=False),
        sa.Column('fat_g', sa.Float(), nullable=False),
        sa.Column('fiber_g', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("meal IN ('breakfast', 'lunch', 'dinner', 'snack')", name='ck_food_log_meal'),
        sa.CheckConstraint('servings > 0 AND servings <= 50', name='ck_food_log_servings'),
    )
    op.create_index('ix_food_logs_owner_id', 'food_logs', ['owner_id'])
    op.create_index('ix_food_logs_eaten_on', 'food_logs', ['eaten_on'])


def downgrade() -> None:
    op.drop_index('ix_food_logs_eaten_on', table_name='food_logs')
    op.drop_index('ix_food_logs_owner_id', table_name='food_logs')
    op.drop_table('food_logs')
    op.drop_index('uq_food_starter_name_serving', table_name='foods')
    op.drop_index('ix_foods_owner_id', table_name='foods')
    op.drop_table('foods')
