"""peptide sheets: allow NULL dose/frequency/when/why text on a missing table cell

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0022'
down_revision: Union[str, None] = '0021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# A real scraped row can genuinely be missing a table cell (parse_sheet already produces None
# positionally for these fields) -- the spec requires that to leave the field empty, never crash
# the whole batch import with a NOT NULL IntegrityError. These are plain nullable-type changes on
# tables that carry no COLLATE clause (unlike peptides.name), so batch_alter_table's
# reflect-and-rebuild here is safe -- verified against a real migrated db, not assumed.


def upgrade() -> None:
    with op.batch_alter_table('peptide_dosing_tiers', schema=None) as batch_op:
        batch_op.alter_column('dose_text', existing_type=sa.String(length=100), nullable=True)
        batch_op.alter_column('frequency_text', existing_type=sa.String(length=100), nullable=True)

    with op.batch_alter_table('peptide_monitoring_tests', schema=None) as batch_op:
        batch_op.alter_column('when_text', existing_type=sa.String(length=200), nullable=True)
        batch_op.alter_column('why_text', existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Restore NOT NULL. Any pre-existing NULL rows get backfilled with an empty string first so
    # the downgrade itself never fails on real data.
    op.execute("UPDATE peptide_dosing_tiers SET dose_text = '' WHERE dose_text IS NULL")
    op.execute("UPDATE peptide_dosing_tiers SET frequency_text = '' WHERE frequency_text IS NULL")
    op.execute("UPDATE peptide_monitoring_tests SET when_text = '' WHERE when_text IS NULL")
    op.execute("UPDATE peptide_monitoring_tests SET why_text = '' WHERE why_text IS NULL")

    with op.batch_alter_table('peptide_dosing_tiers', schema=None) as batch_op:
        batch_op.alter_column('dose_text', existing_type=sa.String(length=100), nullable=False)
        batch_op.alter_column('frequency_text', existing_type=sa.String(length=100), nullable=False)

    with op.batch_alter_table('peptide_monitoring_tests', schema=None) as batch_op:
        batch_op.alter_column('when_text', existing_type=sa.String(length=200), nullable=False)
        batch_op.alter_column('why_text', existing_type=sa.Text(), nullable=False)
