"""protocols: short courses that repeat (days, cadence, per-item run days), and their goal with five suggested peptides

Revision ID: 0085
Revises: 0084
Create Date: 2026-11-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0085'
down_revision: Union[str, None] = '0084'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GOAL = "annual-short-term"
STACK = ("Epithalon", "Thymalin", "Vesugen", "Cartalax", "FOXO4-DRI")


def upgrade() -> None:
    with op.batch_alter_table('protocols') as batch:
        batch.add_column(sa.Column('short_days', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('repeat_months', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('anchor_start', sa.Date(), nullable=True))
        batch.add_column(sa.Column('run_number', sa.Integer(), nullable=False, server_default='1'))
    with op.batch_alter_table('protocol_items') as batch:
        batch.add_column(sa.Column('start_day', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('run_days', sa.Integer(), nullable=True))

    # The five peptides the goal suggests. Any that the library does not have yet is added as a starter entry; nothing existing is touched.
    conn = op.get_bind()
    for position, name in enumerate(STACK):
        found = conn.execute(sa.text("SELECT id FROM peptides WHERE name = :n COLLATE NOCASE"), {"n": name}).scalar()
        if found is None:
            conn.execute(sa.text("INSERT INTO peptides (name, source) VALUES (:n, 'starter')"), {"n": name})
            found = conn.execute(sa.text("SELECT id FROM peptides WHERE name = :n COLLATE NOCASE"), {"n": name}).scalar()
        have = conn.execute(sa.text("SELECT 1 FROM goal_peptides WHERE goal = :g AND peptide_id = :p"), {"g": GOAL, "p": found}).scalar()
        if not have:
            conn.execute(sa.text("INSERT INTO goal_peptides (goal, peptide_id, position) VALUES (:g, :p, :pos)"), {"g": GOAL, "p": found, "pos": position})


def downgrade() -> None:
    op.get_bind().execute(sa.text("DELETE FROM goal_peptides WHERE goal = :g"), {"g": GOAL})
    with op.batch_alter_table('protocol_items') as batch:
        batch.drop_column('run_days')
        batch.drop_column('start_day')
    with op.batch_alter_table('protocols') as batch:
        batch.drop_column('run_number')
        batch.drop_column('anchor_start')
        batch.drop_column('repeat_months')
        batch.drop_column('short_days')
