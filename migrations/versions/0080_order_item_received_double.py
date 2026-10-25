"""order items: a check-in may receive up to double the ordered quantity (a vendor sent extras)

Revision ID: 0080
Revises: 0079
Create Date: 2026-10-25
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0080'
down_revision: Union[str, None] = '0079'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('order_items') as b:
        b.drop_constraint('ck_order_item_received_range', type_='check')
        b.create_check_constraint('ck_order_item_received_range',
                                  'received_quantity IS NULL OR (received_quantity >= 0 AND received_quantity <= quantity * 2)')


def downgrade() -> None:
    with op.batch_alter_table('order_items') as b:
        b.drop_constraint('ck_order_item_received_range', type_='check')
        b.create_check_constraint('ck_order_item_received_range',
                                  'received_quantity IS NULL OR (received_quantity >= 0 AND received_quantity <= quantity)')
